"""Two-tier, confidence-based router.

Tier 1  embedding k-NN classifier: the question embedding (computed once and reused by the cache and
        retrievers) is compared with ~50 labelled exemplars stored in pgvector. Zero extra API calls, ~5 ms.
        Accepted only when confident AND the route needs no decomposition (SQL_ONLY / VECTOR_ONLY / out of scope)
        AND there is no chat history (follow-ups need rewriting).
Tier 2  LLM router (gpt-oss-20b, JSON): handles HYBRID decomposition (parallel vs sequential, sub-questions,
        topic filter), follow-up rewriting and anything tier 1 is unsure about.
Fallback if the LLM fails or is unsure (< ROUTER_LLM_MIN_CONFIDENCE) we choose HYBRID/parallel, because
        querying both sources is the safe failure mode (costs more, never misses a source).
"""
import re
import time
from collections import defaultdict
from functools import lru_cache

from . import config, prompts, providers
from .db import admin_conn
from .llm import chat, parse_json

ROUTES = ["SQL_ONLY", "VECTOR_ONLY", "HYBRID"]
KNN_K = 7


@lru_cache(maxsize=1)
def source_catalog() -> tuple[str, str, tuple[str, ...]]:
    with admin_conn() as conn:
        tables = conn.execute("""
            SELECT table_name, obj_description(('public.'||table_name)::regclass,'pg_class') AS d
            FROM information_schema.tables WHERE table_schema='public' ORDER BY 1""").fetchall()
        docs = conn.execute("""
            SELECT DISTINCT title, metadata->>'topic' AS topic FROM rag.chunks WHERE collection='docs' ORDER BY 1""").fetchall()
    t = "\n".join(f"- {r['table_name']}: {(r['d'] or '').split('.')[0]}" for r in tables)
    d = "\n".join(f"- {r['title']} (topic: {r['topic']})" for r in docs)
    return t, d, tuple(sorted({r["topic"] for r in docs if r["topic"]}))


# Requests for non-university work are out of scope no matter what a model thinks, unless the message
# also clearly asks about university matters ("write an essay" alone vs "essay submission deadline").
OFF_TOPIC_TASK = re.compile(
    r"\b(poem|poems|poetry|haiku|limerick|lyrics|song|songs|story|stories|essay|joke|jokes|riddle|rap|recipe|"
    r"horoscope|weather|movie|movies|film|series|cricket|football|ipl|stock|stocks|bitcoin|crypto|"
    r"translate|translation|capital of|president of|prime minister|"
    r"(write|generate|create|debug|fix|explain)\b.{0,30}\b(code|function|script|program|python|java|javascript|c\+\+|html|css|regex))\b",
    re.I)
UNIVERSITY_HINT = re.compile(
    r"\b(fee|fees|fine|fines|refund|grant|grants|scholarship|course|courses|exam|exams|hall ticket|hostel|gpa|cgpa|"
    r"attendance|credit|credits|semester|term|timetable|placement|placements|internship|department|faculty|"
    r"professor|student|students|enrol\w*|admission|revaluation|university|campus|library|syllabus|assignment|deadline)\b",
    re.I)


def is_off_topic_task(question: str) -> bool:
    return bool(OFF_TOPIC_TASK.search(question)) and not UNIVERSITY_HINT.search(question)


def knn_classify(question: str) -> dict:
    t = time.time()
    qv = providers.embed_query(question)
    if qv is None:
        return {"label": None, "confidence": 0.0, "ms": int((time.time() - t) * 1000), "neighbours": [],
                "disabled": "embeddings unavailable"}
    with admin_conn() as conn:
        rows = conn.execute("""
            SELECT metadata->>'label' AS label, content, 1 - (embedding <=> %s::vector) AS sim
            FROM rag.chunks WHERE collection = 'route_exemplars'
            ORDER BY embedding <=> %s::vector LIMIT %s""", (qv, qv, KNN_K)).fetchall()
    if not rows:
        return {"label": None, "confidence": 0.0, "ms": int((time.time() - t) * 1000), "neighbours": []}
    votes = defaultdict(float)
    for r in rows:
        votes[r["label"]] += max(float(r["sim"]), 0.0) ** 4  # sharpen: closest neighbours dominate
    total = sum(votes.values()) or 1.0
    label = max(votes, key=votes.get)
    return {"label": label, "confidence": round(votes[label] / total, 3), "top_similarity": round(float(rows[0]["sim"]), 3),
            "votes": {k: round(v / total, 3) for k, v in votes.items()}, "ms": int((time.time() - t) * 1000),
            "neighbours": [(r["label"], r["content"], round(float(r["sim"]), 3)) for r in rows[:3]]}


def _profile_str(p):
    return ", ".join(f"{k}={v}" for k, v in p.items() if v not in (None, "")) if p else "not logged in"


def _history_str(h):
    return "\n".join(f"{m['role']}: {m['content'][:400]}" for m in (h or [])[-6:]) or "(none)"


def llm_route(question: str, profile: dict | None, history: list[dict] | None) -> tuple[dict, dict]:
    tables, docs, topics = source_catalog()
    system = prompts.ROUTER_SYSTEM.format(tables=tables, docs=docs, topics=", ".join(topics))
    user = prompts.ROUTER_USER.format(history=_history_str(history), profile=_profile_str(profile), question=question)
    raw, meta = chat(system, user, role="router", json_mode=True)
    out = parse_json(raw)
    if out.get("route") not in ROUTES:
        raise ValueError(f"invalid route {out.get('route')!r}")
    return out, meta


def route(question: str, profile: dict | None = None, history: list[dict] | None = None,
          force_llm: bool = False) -> dict:
    """Returns a routing decision:
    {route, strategy, in_scope, standalone_question, sql_question, doc_question, doc_topic, confidence,
     tier: 'fast'|'llm'|'fallback', reasoning, fast: {...}, ms}"""
    t0 = time.time()
    if is_off_topic_task(question):  # deterministic: no model can be talked into doing these
        return {"route": "VECTOR_ONLY", "strategy": None, "in_scope": False, "standalone_question": question,
                "sql_question": None, "doc_question": None, "doc_topic": None, "confidence": 1.0,
                "tier": "rule", "reasoning": "non-university task (rule)", "fast": {},
                "ms": int((time.time() - t0) * 1000)}
    _, _, topics = source_catalog()
    fast = knn_classify(question)
    base = {"standalone_question": question, "sql_question": None, "doc_question": None, "doc_topic": None,
            "strategy": None, "in_scope": True, "fast": fast}

    fast_ok = (not force_llm and not history and fast["label"] in ("SQL_ONLY", "VECTOR_ONLY", "OUT_OF_SCOPE")
               and fast["confidence"] >= config.ROUTER_FAST_CONFIDENCE and fast.get("top_similarity", 0) >= 0.60)
    if fast_ok:
        oos = fast["label"] == "OUT_OF_SCOPE"
        dec = {**base, "route": "VECTOR_ONLY" if oos else fast["label"], "in_scope": not oos,
               "confidence": fast["confidence"], "tier": "fast",
               "reasoning": f"k-NN classifier: {fast['confidence']:.0%} of neighbour weight is {fast['label']}"}
    else:
        try:
            out, meta = llm_route(question, profile, history)
            conf = float(out.get("confidence") or 0)
            dec = {**base, **{k: v for k, v in out.items() if v not in (None, "null", "")}, "confidence": conf,
                   "tier": "llm", "llm_ms": meta["ms"], "model": meta.get("model")}
            if out["route"] == "HYBRID" and out.get("strategy") not in ("parallel", "sequential"):
                dec["strategy"] = "parallel"
            if conf < config.ROUTER_LLM_MIN_CONFIDENCE and dec.get("in_scope", True):
                dec.update(route="HYBRID", strategy="parallel", tier="fallback",
                           reasoning=f"LLM confidence {conf:.2f} below threshold, querying both sources. "
                                     + str(out.get("reasoning", "")))
        except Exception as e:
            label = fast["label"] if fast["label"] in ROUTES and fast["confidence"] >= 0.5 else "HYBRID"
            dec = {**base, "route": label, "strategy": "parallel" if label == "HYBRID" else None,
                   "confidence": fast["confidence"], "tier": "fallback",
                   "reasoning": f"LLM router unavailable ({type(e).__name__}); used {label}"}
    # Safety net: the LLM said "out of scope" but the classifier is confident it is a university question
    if dec.get("in_scope") in (False, "false") and fast.get("label") in ROUTES and fast.get("confidence", 0) >= 0.5:
        dec.update(in_scope=True, route=fast["label"], strategy="parallel" if fast["label"] == "HYBRID" else None,
                   reasoning=f"scope overridden: k-NN says {fast['label']} ({fast['confidence']:.0%}). " + str(dec.get("reasoning", "")))
    if dec.get("doc_topic") not in topics:
        dec["doc_topic"] = None
    dec["in_scope"] = dec.get("in_scope") not in (False, "false")
    dec["ms"] = int((time.time() - t0) * 1000)
    return dec
