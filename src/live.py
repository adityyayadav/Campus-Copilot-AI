"""Live pipeline monitor: publish/subscribe of per-request pipeline events.

Publisher  each request's worker calls publish() for every pipeline event; it is turned into a compact,
           privacy-safe summary and sent with PostgreSQL NOTIFY on channel `campus_live`.
Listener   one background thread per server process LISTENs on that channel (so events from every uvicorn
           worker reach every process), keeps a short history and fans events out to subscribers.
Subscriber GET /api/live streams them to the developer console as Server-Sent Events.
"""
import json
import logging
import re
import threading
import time
from collections import deque

import psycopg

from . import config
from .db import admin_conn

CHANNEL = "campus_live"
HISTORY = deque(maxlen=200)
_subscribers: list[deque] = []
_sub_lock = threading.Lock()
_listener_started = threading.Event()
log = logging.getLogger("campus_copilot")

_SID = re.compile(r"\bS\d{4}\b", re.I)


def mask_id(student_id: str | None) -> str | None:
    if not student_id:
        return None
    return student_id if student_id == "ADMIN" else student_id[:2] + "••" + student_id[-1]


def _short(text: str | None, n: int) -> str | None:
    if text is None:
        return None
    text = str(text)
    return text if len(text) <= n else text[: n - 1] + "…"


def summarize(ev: dict) -> dict:
    """Pipeline event -> compact summary (NOTIFY payloads must stay well under 8 kB)."""
    t = ev.get("type")
    if t == "guard":
        inj = ev.get("injection") or {}
        return {"stage": "guard", "status": "passed", "score": inj.get("classifier_score")}
    if t == "cache":
        return {"stage": "cache", "status": "hit" if ev.get("hit") else "miss", "similarity": ev.get("similarity")}
    if t == "blocked":
        return {"stage": "guard", "status": "blocked", "code": ev.get("code"),
                "score": (ev.get("detail") or {}).get("classifier_score")}
    if t == "error":
        return {"stage": "error", "status": "error", "message": _short(ev.get("message"), 160)}
    if t == "final":
        u = ev.get("usage") or {}
        return {"stage": "final", "status": "done", "latency_ms": ev.get("latency_ms"), "tokens": u.get("tokens"),
                "llm_calls": u.get("llm_calls"), "cache_hit": bool((ev.get("cache") or {}).get("hit")),
                "grounding": (ev.get("grounding") or {}).get("status"), "route": ev.get("route")}
    if t != "step":
        return {}
    node, d = ev.get("node"), ev.get("data") or {}
    trace = {x.get("node"): x.get("ms") for x in d.get("trace") or []}
    if node == "router":
        r = d.get("router") or {}
        fast = r.get("fast") or {}
        return {"stage": "router", "status": "done", "ms": r.get("ms"), "route": d.get("route"), "strategy": r.get("strategy"),
                "tier": r.get("tier"), "confidence": r.get("confidence"), "in_scope": r.get("in_scope", True),
                "reasoning": _short(r.get("reasoning"), 160), "knn": fast.get("votes"), "topic": r.get("doc_topic"),
                "sql_question": _short(r.get("sql_question"), 160), "doc_question": _short(r.get("doc_question"), 160)}
    if node == "docs":
        docs = d.get("docs") or {}
        chunks = docs.get("chunks") or []
        return {"stage": "docs", "status": "done", "ms": trace.get("docs"), "reranked": docs.get("reranked"),
                "filters": docs.get("filters"), "relaxed": docs.get("filter_relaxed"),
                "timings": docs.get("timings"),
                "sources": [{"title": _short(c.get("title"), 60), "section": _short(c.get("section"), 60),
                             "score": c.get("rerank_score"), "page": c.get("page")} for c in chunks[:5]]}
    if node == "sql":
        s = d.get("sql") or {}
        return {"stage": "sql", "status": "error" if s.get("error") else "done", "ms": s.get("ms"),
                "tables": (s.get("tables") or [])[:6], "rows": len(s.get("rows") or []), "attempts": s.get("attempts"),
                "used_policy": s.get("used_policy"), "sql": _short(s.get("sql"), 900), "error": _short(s.get("error"), 200)}
    if node == "synthesize":
        return {"stage": "synthesize", "status": "done", "ms": trace.get("synthesize")}
    if node == "grounding":
        g = d.get("grounding") or {}
        return {"stage": "grounding", "status": g.get("status"), "ms": trace.get("grounding"),
                "checked": g.get("checked_numbers"), "unsupported": (g.get("unsupported") or [])[:6]}
    if node == "out_of_scope":
        return {"stage": "out_of_scope", "status": "done", "ms": trace.get("out_of_scope")}
    return {}


def publish(req_id: str, payload: dict) -> None:
    """Fire-and-forget: telemetry must never slow down or break a request."""
    if not payload:
        return
    msg = json.dumps({"id": req_id, "ts": time.time(), **payload}, default=str)
    if len(msg) > 7500:
        payload = {k: v for k, v in payload.items() if k not in ("sql", "sources", "knn", "timings")}
        msg = json.dumps({"id": req_id, "ts": time.time(), **payload, "truncated": True}, default=str)
    try:
        with admin_conn() as conn:
            conn.execute("SELECT pg_notify(%s, %s)", (CHANNEL, msg))
    except Exception as e:  # noqa: BLE001
        log.debug("live publish failed: %s", e)


def request_started(req_id: str, question: str, student_id: str | None, role: str) -> None:
    publish(req_id, {"stage": "start", "status": "running", "role": role, "user": mask_id(student_id),
                     "question": _short(_SID.sub(lambda m: mask_id(m.group(0).upper()), question), 240)})


# ------------------------------------------------------------------ listener (one per process)
def _listen_forever() -> None:
    while True:
        try:
            with psycopg.connect(config.DATABASE_URL, autocommit=True) as conn:
                conn.execute(f"LISTEN {CHANNEL}")
                for note in conn.notifies():
                    try:
                        ev = json.loads(note.payload)
                    except ValueError:
                        continue
                    HISTORY.append(ev)
                    with _sub_lock:
                        for q in list(_subscribers):
                            q.append(ev)
        except Exception as e:  # noqa: BLE001  reconnect after DB restarts
            log.warning("live listener reconnecting: %s", e)
            time.sleep(2)


def ensure_listener() -> None:
    if not _listener_started.is_set():
        _listener_started.set()
        threading.Thread(target=_listen_forever, name="live-listener", daemon=True).start()


def subscribe() -> deque:
    q: deque = deque(maxlen=1000)
    with _sub_lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: deque) -> None:
    with _sub_lock:
        if q in _subscribers:
            _subscribers.remove(q)
