"""Request pipeline: guardrails -> semantic cache -> LangGraph -> cache store -> telemetry.
Yields events so the web UI can show each stage live (Server-Sent Events)."""
import logging
import re
import time
from typing import Iterator

from . import cache, providers
from .db import admin_conn
from .graph import build_graph
from .guardrails import GuardrailViolation, check_authorization, check_injection, rate_limit, sanitize
from .llm import start_metering

log = logging.getLogger("campus_copilot")
if not log.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(_h)
    log.setLevel(logging.INFO)

_SID = re.compile(r"^S\d{4}$")
ADMIN_ID = "ADMIN"
ADMIN_PROFILE = {"role": "admin", "name": "Registrar Office", "department": "Administration",
                 "access": "all students and records"}


def load_profile(student_id: str | None) -> dict | None:
    """Demo sign-in. In production the role and student id come from the university SSO token."""
    if not student_id:
        return None
    if student_id == ADMIN_ID:
        return dict(ADMIN_PROFILE)
    if not _SID.match(student_id):
        raise GuardrailViolation("invalid_input", "Invalid student id.")
    with admin_conn() as conn:
        r = conn.execute("""SELECT s.student_id, s.name, s.dept_id, d.name AS department, s.year, s.gpa,
                                   s.attendance_pct, s.is_hosteller
                            FROM students s JOIN departments d USING (dept_id) WHERE s.student_id = %s""",
                         (student_id,)).fetchone()
    if not r:
        raise GuardrailViolation("unauthorized", "Unknown student.")
    return {"role": "student", **{k: (float(v) if k in ("gpa", "attendance_pct") else v) for k, v in r.items()}}


def _log(**kw):
    try:
        with admin_conn() as conn:
            conn.execute("""INSERT INTO ops.request_log (route, strategy, router_tier, confidence, cache_hit, blocked,
                                grounding, sql_ok, latency_ms, llm_calls, tokens)
                            VALUES (%(route)s, %(strategy)s, %(router_tier)s, %(confidence)s, %(cache_hit)s, %(blocked)s,
                                    %(grounding)s, %(sql_ok)s, %(latency_ms)s, %(llm_calls)s, %(tokens)s)""",
                         {"route": None, "strategy": None, "router_tier": None, "confidence": None, "cache_hit": False,
                          "blocked": None, "grounding": None, "sql_ok": None, "latency_ms": None, "llm_calls": 0,
                          "tokens": 0, **kw})
            conn.commit()
    except Exception:
        pass  # telemetry must never break a request


def _public_state(st: dict) -> dict:
    """What the UI needs (and what is cached): no internal-only fields."""
    s = st.get("sql_result")
    d = st.get("doc_result")
    return {
        "answer": st.get("answer", ""),
        "route": st.get("route"),
        "router": {k: v for k, v in (st.get("router") or {}).items()},
        "sql": None if not s else {k: s.get(k) for k in ("question", "sql", "explanation", "columns", "rows", "error",
                                                          "attempts", "tables", "steps", "ms", "used_policy")},
        "docs": None if not d else {**{k: d.get(k) for k in ("query", "filters", "filter_relaxed", "hyde", "reranked",
                                                              "timings")},
                                    "chunks": [{k: c.get(k) for k in ("title", "section", "content", "collection", "source",
                                                                      "rerank_score", "dense_rank", "bm25_rank", "rrf",
                                                                      "low_relevance")}
                                              | {"page": (c.get("metadata") or {}).get("page"),
                                                 "doc_type": (c.get("metadata") or {}).get("doc_type")}
                                              for c in d.get("chunks", [])]},
        "grounding": st.get("grounding"),
        "trace": st.get("trace", []),
    }


def handle(question: str, student_id: str | None, history: list[dict] | None, use_hyde: bool = False,
           use_rerank: bool = True, client_id: str = "local") -> Iterator[dict]:
    t0 = time.time()
    usage = start_metering()
    embed_calls0 = providers.STATS["embed_api_calls"]
    history = (history or [])[-6:]
    try:
        rate_limit(client_id)
        profile = load_profile(student_id)
        q = sanitize(question)
        inj = check_injection(q)
        check_authorization(q, profile)
        yield {"type": "guard", "status": "passed", "injection": inj}

        scope = (profile or {}).get("student_id") or (profile or {}).get("role") or "guest"
        if not history:
            hit = cache.lookup(q, scope)
            if hit:
                resp = hit["response"]
                ms = int((time.time() - t0) * 1000)
                resp.update(cache={"hit": True, "matched_question": hit["matched_question"],
                                   "similarity": hit["similarity"]},
                            usage={**usage, "embed_api_calls": providers.STATS["embed_api_calls"] - embed_calls0},
                            latency_ms=ms)
                yield {"type": "cache", "hit": True, "matched_question": hit["matched_question"],
                       "similarity": hit["similarity"]}
                yield {"type": "final", **resp}
                _log(route=resp.get("route"), cache_hit=True, latency_ms=ms, grounding=(resp.get("grounding") or {}).get("status"))
                return
            yield {"type": "cache", "hit": False}

        state = {"question": q, "profile": profile, "history": history, "use_hyde": use_hyde,
                 "use_rerank": use_rerank, "trace": []}
        final = dict(state)
        for update in build_graph().stream(state, stream_mode="updates"):
            for node, delta in update.items():
                delta = delta or {}
                for k, v in delta.items():
                    final[k] = final.get(k, []) + v if k == "trace" else v
                yield {"type": "step", "node": node, "data": _public_state(final)}

        resp = _public_state(final)
        ms = int((time.time() - t0) * 1000)
        resp.update(cache={"hit": False}, latency_ms=ms,
                    usage={**usage, "embed_api_calls": providers.STATS["embed_api_calls"] - embed_calls0})
        yield {"type": "final", **resp}

        r = resp["router"]
        sql_ok = None if not resp["sql"] else resp["sql"]["error"] is None
        g = (resp.get("grounding") or {}).get("status")
        if not history and r.get("in_scope", True) and sql_ok is not False and g in ("grounded", "revised"):
            cacheable = {k: v for k, v in resp.items() if k not in ("usage", "latency_ms", "cache")}
            cache.store(q, scope, resp["route"], uses_live_data=resp["sql"] is not None, response=cacheable)
        _log(route=resp["route"], strategy=r.get("strategy"), router_tier=r.get("tier"), confidence=r.get("confidence"),
             grounding=g, sql_ok=sql_ok, latency_ms=ms, llm_calls=usage["llm_calls"], tokens=usage["tokens"])
    except GuardrailViolation as e:
        log.info("blocked %s: %r", e.code, question[:120])
        _log(blocked=e.code, latency_ms=int((time.time() - t0) * 1000))
        yield {"type": "blocked", "code": e.code, "message": e.message, "detail": e.detail}
    except Exception as e:  # never leak stack traces to the client, but always log them server-side
        log.exception("request failed: %r", question[:120])
        _log(blocked="error", latency_ms=int((time.time() - t0) * 1000))
        limited = "RateLimit" in type(e).__name__ or "429" in str(e)[:400]
        yield {"type": "error", "message": (
            "The AI service is at its usage limit right now. Please try again in a few minutes." if limited
            else f"Something went wrong ({type(e).__name__}). Please try again.")}


def metrics() -> dict:
    with admin_conn() as conn:
        tot = conn.execute("""
            SELECT count(*) AS requests,
                   count(*) FILTER (WHERE cache_hit) AS cache_hits,
                   count(*) FILTER (WHERE blocked IS NOT NULL AND blocked <> 'error') AS blocked,
                   count(*) FILTER (WHERE router_tier = 'fast') AS fast_routed,
                   count(*) FILTER (WHERE router_tier IS NOT NULL) AS routed,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE blocked IS NULL) AS p50,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE blocked IS NULL) AS p95,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE cache_hit) AS p50_cached,
                   avg(tokens) FILTER (WHERE NOT cache_hit AND blocked IS NULL) AS avg_tokens,
                   avg(llm_calls) FILTER (WHERE NOT cache_hit AND blocked IS NULL) AS avg_llm_calls,
                   count(*) FILTER (WHERE sql_ok) AS sql_ok, count(*) FILTER (WHERE sql_ok IS NOT NULL) AS sql_total,
                   count(*) FILTER (WHERE grounding = 'grounded') AS grounded,
                   count(*) FILTER (WHERE grounding IN ('grounded','revised')) AS grounding_total
            FROM ops.request_log""").fetchone()
        routes = conn.execute("""SELECT COALESCE(route || CASE WHEN strategy IS NOT NULL THEN ' · ' || strategy ELSE '' END,
                                        'blocked') AS k, count(*) AS n
                                 FROM ops.request_log GROUP BY 1 ORDER BY 2 DESC""").fetchall()
        blocked = conn.execute("""SELECT blocked AS k, count(*) AS n FROM ops.request_log
                                  WHERE blocked IS NOT NULL GROUP BY 1 ORDER BY 2 DESC""").fetchall()
        recent = conn.execute("""SELECT ts, route, strategy, router_tier, cache_hit, blocked, latency_ms, tokens
                                 FROM ops.request_log ORDER BY ts DESC LIMIT 12""").fetchall()
    f = lambda x: None if x is None else round(float(x), 1)  # noqa: E731
    return {
        "requests": tot["requests"], "cache_hits": tot["cache_hits"], "blocked": tot["blocked"],
        "cache_hit_rate": f(100 * tot["cache_hits"] / tot["requests"]) if tot["requests"] else None,
        "fast_route_rate": f(100 * tot["fast_routed"] / tot["routed"]) if tot["routed"] else None,
        "p50_ms": f(tot["p50"]), "p95_ms": f(tot["p95"]), "p50_cached_ms": f(tot["p50_cached"]),
        "avg_tokens": f(tot["avg_tokens"]), "avg_llm_calls": f(tot["avg_llm_calls"]),
        "sql_success_rate": f(100 * tot["sql_ok"] / tot["sql_total"]) if tot["sql_total"] else None,
        "grounded_rate": f(100 * tot["grounded"] / tot["grounding_total"]) if tot["grounding_total"] else None,
        "routes": [dict(r) for r in routes], "blocked_by_type": [dict(r) for r in blocked],
        "recent": [{**dict(r), "ts": r["ts"].isoformat()} for r in recent],
        "embedding_cache": dict(providers.STATS),
    }
