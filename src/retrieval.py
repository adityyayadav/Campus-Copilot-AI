"""Hybrid retrieval entirely inside PostgreSQL:

  dense   : pgvector cosine (HNSW)                    -> semantic match
  sparse  : Okapi BM25 computed in SQL over rag.chunk_terms (Postgres english stemmer)
  fusion  : Reciprocal Rank Fusion (RRF)
  rerank  : hosted cross-encoder (Cohere / Jina) -> relevance score used as a Self-RAG style filter
All stages accept JSONB metadata filters.
"""
import json
import time

from . import config, providers
from .db import admin_conn

BM25_K1, BM25_B = 1.2, 0.75

_BM25_SQL = """
WITH q AS (SELECT DISTINCT lexeme AS term FROM unnest(to_tsvector('english', %(q)s))),
docs AS (SELECT id, doc_len FROM rag.chunks WHERE collection = ANY(%(cols)s) AND metadata @> %(filt)s::jsonb
             AND NOT metadata @> '{"quarantined": true}'::jsonb),
stats AS (SELECT count(*)::float AS n, GREATEST(avg(doc_len), 1)::float AS avgdl FROM docs),
df AS (
    SELECT t.term, count(*)::float AS df
    FROM rag.chunk_terms t JOIN docs d ON d.id = t.chunk_id JOIN q ON q.term = t.term
    GROUP BY t.term
)
SELECT t.chunk_id AS id,
       SUM( ln(1 + (stats.n - df.df + 0.5) / (df.df + 0.5))
            * (t.tf * (%(k1)s + 1)) / (t.tf + %(k1)s * (1 - %(b)s + %(b)s * d.doc_len / stats.avgdl)) ) AS score
FROM rag.chunk_terms t
JOIN df ON df.term = t.term
JOIN docs d ON d.id = t.chunk_id
CROSS JOIN stats
GROUP BY t.chunk_id
ORDER BY score DESC
LIMIT %(k)s
"""

_DENSE_SQL = """
SELECT id, 1 - (embedding <=> %(v)s::vector) AS score
FROM rag.chunks
WHERE collection = ANY(%(cols)s) AND metadata @> %(filt)s::jsonb
  AND NOT metadata @> '{"quarantined": true}'::jsonb
ORDER BY embedding <=> %(v)s::vector
LIMIT %(k)s
"""


def expand_to_parent_sections(chunks: list[dict], max_chunks: int = 8) -> list[dict]:
    """Small-to-big retrieval: rules are often split across sibling sub-sections (e.g. Tier A / Tier B / Tier C).
    For every retrieved document chunk, pull all chunks under the same top-level heading of the same document,
    in reading order, so the SQL agent receives the complete rule set rather than a fragment."""
    docs = [c for c in chunks if c.get("collection") == "docs"]
    if not docs:
        return chunks
    keys = list(dict.fromkeys((c["source"], c["section"].split(" > ")[0]) for c in docs))
    out, seen = [], set()
    with admin_conn() as conn:
        for source, top in keys:
            rows = conn.execute("""
                SELECT id, collection, source, title, section, content, metadata FROM rag.chunks
                WHERE collection = 'docs' AND source = %s AND (section = %s OR section LIKE %s)
                  AND NOT metadata @> '{"quarantined": true}'::jsonb
                ORDER BY (metadata->>'chunk_index')::int""", (source, top, top + " > %")).fetchall()
            for r in rows:
                if r["id"] not in seen and len(out) < max_chunks:
                    seen.add(r["id"])
                    out.append({**r, "expanded": True, "rrf": 0})
    return out + [c for c in chunks if c.get("collection") != "docs"][: max(0, max_chunks - len(out))]


def hybrid_search(query: str, collections: list[str], filters: dict | None = None,
                  top_n: int = config.RERANK_TOP_N, use_rerank: bool = True,
                  dense_query: str | None = None) -> dict:
    """Returns {"chunks": [...], "timings": {...}, "reranked": bool}.
    dense_query lets HyDE pass a hypothetical answer for the vector side while BM25 uses the real question."""
    filt = json.dumps(filters or {})
    timings = {}
    t = time.time()
    qvec = providers.embed_query(dense_query or query)  # None -> keyword (BM25) retrieval only
    timings["embed_ms"] = int((time.time() - t) * 1000)
    if qvec is None:
        timings["dense_skipped"] = "embeddings unavailable"

    with admin_conn() as conn:
        t = time.time()
        dense = [] if qvec is None else conn.execute(
            _DENSE_SQL, {"v": qvec, "cols": collections, "filt": filt, "k": config.DENSE_K}).fetchall()
        timings["dense_ms"] = int((time.time() - t) * 1000)
        t = time.time()
        sparse = conn.execute(_BM25_SQL, {"q": query, "cols": collections, "filt": filt, "k": config.BM25_K,
                                          "k1": BM25_K1, "b": BM25_B}).fetchall()
        timings["bm25_ms"] = int((time.time() - t) * 1000)

        # Reciprocal Rank Fusion
        fused: dict[int, dict] = {}
        for rank, r in enumerate(dense, 1):
            fused.setdefault(r["id"], {"rrf": 0.0})
            fused[r["id"]].update(dense_rank=rank, dense_score=round(float(r["score"]), 4))
            fused[r["id"]]["rrf"] += 1 / (config.RRF_K + rank)
        for rank, r in enumerate(sparse, 1):
            fused.setdefault(r["id"], {"rrf": 0.0})
            fused[r["id"]].update(bm25_rank=rank, bm25_score=round(float(r["score"]), 3))
            fused[r["id"]]["rrf"] += 1 / (config.RRF_K + rank)
        ranked_ids = sorted(fused, key=lambda i: fused[i]["rrf"], reverse=True)[:20]
        if not ranked_ids:
            return {"chunks": [], "timings": timings, "reranked": False}
        rows = {r["id"]: r for r in conn.execute(
            "SELECT id, collection, source, title, section, content, embed_text, metadata FROM rag.chunks WHERE id = ANY(%s)",
            (ranked_ids,)).fetchall()}

    candidates = [{**rows[i], **fused[i], "rrf": round(fused[i]["rrf"], 5)} for i in ranked_ids if i in rows]

    reranked = False
    if use_rerank:
        t = time.time()
        try:
            res = providers.rerank(query, [c["embed_text"] for c in candidates], top_n=min(top_n * 2, len(candidates)))
        except Exception as e:  # reranker outage must not kill the answer
            res, timings["rerank_error"] = None, str(e)[:200]
        timings["rerank_ms"] = int((time.time() - t) * 1000)
        if res is not None:
            reranked = True
            out = []
            for idx, score in res:
                c = candidates[idx]
                c["rerank_score"] = round(float(score), 4)
                out.append(c)
            candidates = out

    if reranked:
        kept = [c for c in candidates if c["rerank_score"] >= config.RERANK_MIN_SCORE][:top_n]
        if not kept:                       # nothing judged relevant: keep the single best for transparency
            kept = candidates[:1]
            for c in kept:
                c["low_relevance"] = True
        candidates = kept
    else:
        candidates = candidates[:top_n]
    for c in candidates:
        c.pop("embed_text", None)
    return {"chunks": candidates, "timings": timings, "reranked": reranked}
