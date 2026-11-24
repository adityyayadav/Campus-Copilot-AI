"""FastAPI backend.   Run:  uvicorn src.api:app --port 8000
Serves the API under /api and the built React app (web/dist) at /."""
import json
import queue
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import config, providers
from .chunking import _front_matter
from .db import admin_conn, pool_stats
from .service import handle

app = FastAPI(title="Campus Copilot AI", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

PERSONAS = ("S1023", "S1045")  # demo students; plus the admin account and guest

# Bounded concurrency: a fixed pool runs the AI pipelines; a bounded queue absorbs bursts; beyond that we shed load
# with HTTP 503 instead of letting threads, DB connections and provider rate limits pile up.
_executor = ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT_CHATS, thread_name_prefix="chat")
_slots = threading.BoundedSemaphore(config.MAX_CONCURRENT_CHATS + config.MAX_QUEUED_CHATS)
_inflight = {"n": 0}
_inflight_lock = threading.Lock()


class Turn(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=200_000)

    @field_validator("content")
    @classmethod
    def clip(cls, v: str) -> str:
        return v[:1500]  # the router only reads the start of earlier turns; long tables must not break follow-ups


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=config.MAX_QUESTION_CHARS)
    student_id: str | None = Field(default=None, pattern=r"^(S\d{4}|ADMIN)$")
    history: list[Turn] = Field(default_factory=list, max_length=40)

    @field_validator("history")
    @classmethod
    def last_turns(cls, v: list) -> list:
        return v[-12:]
    use_hyde: bool = False
    use_rerank: bool = True

    @field_validator("question")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


@app.get("/api/health")
def health():
    try:
        with admin_conn() as conn:
            n = conn.execute("SELECT count(*) AS n FROM rag.chunks").fetchone()["n"]
        db = "ok"
    except Exception as e:
        n, db = 0, f"error: {type(e).__name__}"
    return {"db": db, "indexed_chunks": n,
            "capacity": {"max_concurrent_chats": config.MAX_CONCURRENT_CHATS, "max_queued": config.MAX_QUEUED_CHATS,
                         "inflight": _inflight["n"], "db_pools": pool_stats()},
            "llm": {"provider": "groq", "main": config.MAIN_MODEL, "router": config.ROUTER_MODEL,
                    "fallback": f"gemini:{config.GEMINI_CHAT_MODEL}" if config.GOOGLE_API_KEY else None,
                    "configured": bool(config.GROQ_API_KEY)},
            "embeddings": {"provider": config.EMBED_PROVIDER, "dim": config.EMBED_DIM,
                           "configured": bool({"gemini": config.GOOGLE_API_KEY, "cohere": config.COHERE_API_KEY,
                                               "jina": config.JINA_API_KEY}.get(config.EMBED_PROVIDER))},
            "reranker": config.RERANK_PROVIDER, "guard_model": config.GUARD_MODEL,
            "embedding_status": providers.embeddings_status(),
            "restart_needed": config.env_changed_since_start()}


@app.get("/api/personas")
def personas():
    with admin_conn() as conn:
        rows = conn.execute("""SELECT s.student_id, s.name, s.dept_id, d.name AS department, s.year, s.gpa,
                                      s.attendance_pct, s.is_hosteller
                               FROM students s JOIN departments d USING (dept_id)
                               WHERE s.student_id = ANY(%s) ORDER BY s.student_id""", (list(PERSONAS),)).fetchall()
    students = [{"role": "student", **{k: (float(v) if k in ("gpa", "attendance_pct") else v) for k, v in r.items()}}
                for r in rows]
    admin = {"student_id": "ADMIN", "role": "admin", "name": "Registrar Office", "dept_id": "ADMIN",
             "department": "Administration", "year": None, "gpa": None, "attendance_pct": None}
    return [admin] + students


@app.get("/api/documents/{name}")
def document(name: str):
    """Full text of an indexed policy document, for the citation viewer. Only files that are actually in the
    index can be read (allow-list from the database), so arbitrary paths are impossible."""
    allowed = _indexed_documents()
    if name not in allowed:
        return JSONResponse(status_code=404, content={"error": "document not found"})
    path = config.DOCS_DIR / name
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader
        body = "\n\n".join((p.extract_text() or "") for p in PdfReader(str(path)).pages)
        side = path.with_suffix(".meta.json")
        meta = json.loads(side.read_text(encoding="utf-8")) if side.exists() else {}
    else:
        meta, body = _front_matter(path.read_text(encoding="utf-8"))
    return {"source": name, "title": allowed[name], "meta": meta, "markdown": body.strip()}


@app.get("/api/documents/{name}/file")
def document_file(name: str):
    """The original document file (used by the PDF evidence viewer). Same allow-list as above."""
    if name not in _indexed_documents():
        return JSONResponse(status_code=404, content={"error": "document not found"})
    path = config.DOCS_DIR / name
    media = "application/pdf" if path.suffix.lower() == ".pdf" else "text/markdown; charset=utf-8"
    return FileResponse(path, media_type=media, headers={"Cache-Control": "private, max-age=3600",
                                                         "Content-Disposition": f'inline; filename="{name}"'})


@lru_cache(maxsize=1)
def _indexed_documents() -> dict[str, str]:
    with admin_conn() as conn:
        rows = conn.execute("SELECT DISTINCT source, title FROM rag.chunks WHERE collection = 'docs'").fetchall()
    return {r["source"]: r["title"] for r in rows}


@app.post("/api/chat")
def chat(req: ChatRequest, request: Request):
    client = request.client.host if request.client else "unknown"
    if not _slots.acquire(blocking=False):
        return JSONResponse(status_code=503, headers={"Retry-After": "5"},
                            content={"detail": "The assistant is busy right now. Please try again in a few seconds."})
    q: queue.Queue = queue.Queue()

    def worker():  # runs on the bounded pool; per-request usage metering stays in this thread's context
        with _inflight_lock:
            _inflight["n"] += 1
        try:
            for ev in handle(req.question, req.student_id, [t.model_dump() for t in req.history],
                             req.use_hyde, req.use_rerank, client):
                q.put(ev)
        finally:
            with _inflight_lock:
                _inflight["n"] -= 1
            _slots.release()
            q.put(None)

    _executor.submit(worker)

    def stream():
        while (ev := q.get()) is not None:
            yield f"data: {json.dumps(ev, default=str)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


from fastapi.exceptions import RequestValidationError  # noqa: E402
import logging  # noqa: E402

_alog = logging.getLogger("campus_copilot")


@app.exception_handler(RequestValidationError)
async def invalid(_, exc):
    _alog.warning("rejected request: %s", [(e.get("loc"), e.get("msg")) for e in exc.errors()[:3]])
    return JSONResponse(status_code=422, content={"detail": [{"msg": e.get("msg", "invalid"), "loc": e.get("loc")}
                                                             for e in exc.errors()[:3]]})


@app.exception_handler(Exception)
async def unhandled(_, exc):
    return JSONResponse(status_code=500, content={"error": "internal_error"})


# ------------------------------------------------------------------ static frontend
DIST = config.ROOT / "web" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        if path.startswith("api/"):  # unknown API routes are real 404s, not the app shell
            return JSONResponse(status_code=404, content={"error": "not found"})
        f = DIST / path
        if path and f.is_file() and DIST in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(DIST / "index.html")
