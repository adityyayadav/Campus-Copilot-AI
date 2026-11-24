"""Semantic answer cache in pgvector.

A new question hits the cache when ALL of these hold:
  * same scope (guest, or the same signed-in student): personal answers never cross users
  * cosine similarity >= CACHE_SIMILARITY with a cached question
  * identical "entity signature" (course codes, IDs, numbers), so "seats in CS351" never serves "seats in CS352"
  * not expired: policy-only answers live 24 h, anything built on live SQL data only 5 minutes
Only clean answers are stored (no follow-up context, no SQL error, grounding passed).
"""
import json
import re

from . import config, providers
from .db import admin_conn

_ENTITY = re.compile(r"\b[A-Za-z]*\d[\w.-]*\b")


def entity_signature(q: str) -> str:
    return " ".join(sorted({m.upper() for m in _ENTITY.findall(q)}))


def lookup(question: str, scope: str) -> dict | None:
    qv = providers.embed_query(question)
    if qv is None:
        return None
    with admin_conn() as conn:
        row = conn.execute("""
            SELECT id, question, response, 1 - (embedding <=> %(v)s::vector) AS sim
            FROM rag.semantic_cache
            WHERE scope = %(s)s AND entities = %(e)s AND expires_at > now()
            ORDER BY embedding <=> %(v)s::vector LIMIT 1""",
                           {"v": qv, "s": scope, "e": entity_signature(question)}).fetchone()
        if not row or float(row["sim"]) < config.CACHE_SIMILARITY:
            return None
        conn.execute("UPDATE rag.semantic_cache SET hits = hits + 1 WHERE id = %s", (row["id"],))
        conn.commit()
    return {"response": row["response"], "matched_question": row["question"], "similarity": round(float(row["sim"]), 4)}


def store(question: str, scope: str, route: str, uses_live_data: bool, response: dict) -> None:
    qv = providers.embed_query(question)
    if qv is None:
        return
    ttl = config.CACHE_TTL_LIVE_S if uses_live_data else config.CACHE_TTL_DOCS_S
    with admin_conn() as conn:
        conn.execute("""
            INSERT INTO rag.semantic_cache (scope, question, entities, embedding, route, response, expires_at)
            VALUES (%s, %s, %s, %s::vector, %s, %s::jsonb, now() + make_interval(secs => %s))""",
                     (scope, question, entity_signature(question), qv, route, json.dumps(response, default=str), ttl))
        conn.execute("DELETE FROM rag.semantic_cache WHERE expires_at < now()")
        conn.commit()
