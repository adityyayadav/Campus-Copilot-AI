"""Build the unified index inside PostgreSQL (schema rag):

  rag.chunks       one row per chunk (docs | schema_cards | table_rows) + pgvector embedding + JSONB metadata
  rag.chunk_terms  inverted index (term, tf) produced by Postgres' english text-search stemmer -> Okapi BM25 in SQL

Run:  python -m scripts.ingest
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config, providers  # noqa: E402
from src.chunking import chunk_file  # noqa: E402
from src.db import admin_conn  # noqa: E402
from src.guardrails import heuristic_injection, prompt_guard_score  # noqa: E402
from src.route_exemplars import EXEMPLARS  # noqa: E402
from src.schema_mapping import build_schema_cards, build_table_rows  # noqa: E402

DDL = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS rag;
DROP TABLE IF EXISTS rag.chunk_terms, rag.chunks;
CREATE TABLE rag.chunks (
    id          BIGSERIAL PRIMARY KEY,
    collection  TEXT NOT NULL,            -- docs | schema_cards | table_rows
    source      TEXT NOT NULL,
    title       TEXT NOT NULL,
    section     TEXT NOT NULL,
    content     TEXT NOT NULL,            -- shown to the LLM
    embed_text  TEXT NOT NULL,            -- contextual header + content (embedded + BM25 indexed)
    metadata    JSONB NOT NULL DEFAULT '{{}}',
    doc_len     INT NOT NULL DEFAULT 0,   -- number of index terms, for BM25 length normalisation
    embedding   vector({config.EMBED_DIM})          -- NULL when no embedding key: BM25-only mode
);
CREATE INDEX ON rag.chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON rag.chunks (collection);
CREATE INDEX ON rag.chunks USING gin (metadata jsonb_path_ops);
CREATE TABLE rag.chunk_terms (
    chunk_id BIGINT NOT NULL REFERENCES rag.chunks(id) ON DELETE CASCADE,
    term     TEXT NOT NULL,
    tf       INT NOT NULL
);
CREATE INDEX ON rag.chunk_terms (term);

-- Semantic answer cache (invalidated on every re-ingest because embeddings / content may change)
DROP TABLE IF EXISTS rag.semantic_cache;
CREATE TABLE rag.semantic_cache (
    id           BIGSERIAL PRIMARY KEY,
    scope        TEXT NOT NULL,           -- 'guest' or the student_id: answers never leak across users
    question     TEXT NOT NULL,
    entities     TEXT NOT NULL,           -- codes / numbers in the question; must match exactly
    embedding    vector({config.EMBED_DIM}) NOT NULL,
    route        TEXT NOT NULL,
    response     JSONB NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    hits         INT NOT NULL DEFAULT 0
);
CREATE INDEX ON rag.semantic_cache USING hnsw (embedding vector_cosine_ops);
CREATE INDEX ON rag.semantic_cache (scope, expires_at);

-- Request telemetry for the metrics dashboard
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.request_log (
    id            BIGSERIAL PRIMARY KEY,
    ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
    route         TEXT,
    strategy      TEXT,
    router_tier   TEXT,
    confidence    REAL,
    cache_hit     BOOLEAN NOT NULL DEFAULT FALSE,
    blocked       TEXT,
    grounding     TEXT,
    sql_ok        BOOLEAN,
    latency_ms    INT,
    llm_calls     INT,
    tokens        INT
);
"""

BUILD_TERMS = """
INSERT INTO rag.chunk_terms (chunk_id, term, tf)
SELECT c.id, t.lexeme, COALESCE(array_length(t.positions, 1), 1)
FROM rag.chunks c, unnest(to_tsvector('english', c.embed_text)) AS t;
UPDATE rag.chunks c SET doc_len = s.len
FROM (SELECT chunk_id, SUM(tf) AS len FROM rag.chunk_terms GROUP BY chunk_id) s
WHERE s.chunk_id = c.id;
"""


def scan_documents(chunks: list[dict]) -> None:
    """Documents are untrusted input: score every chunk with heuristics + Llama Prompt Guard 2.
    Flagged chunks are kept for audit but excluded from retrieval (metadata.quarantined = true)."""
    flagged = 0
    for c in chunks:
        hit = heuristic_injection(c["content"])
        score = prompt_guard_score(c["content"])
        time.sleep(0.25)  # stay well inside free-tier RPM
        c["metadata"]["injection_score"] = score
        c["metadata"]["quarantined"] = bool(hit) or (score is not None and score >= config.INJECTION_THRESHOLD)
        flagged += c["metadata"]["quarantined"]
        if c["metadata"]["quarantined"]:
            print(f"  !! quarantined chunk from {c['source']} > {c['section']} (score={score}, match={hit!r})")
    print(f"  injection scan: {len(chunks)} doc chunks checked, {flagged} quarantined")


def main():
    t0 = time.time()
    chunks = []
    for p in sorted(config.DOCS_DIR.glob("*")):
        cs = chunk_file(p)
        print(f"  doc  {p.name:<32} {len(cs):>3} chunks")
        chunks += cs
    scan_documents(chunks)
    cards = build_schema_cards()
    rows = build_table_rows()
    exemplars = [{"collection": "route_exemplars", "source": "router", "title": label, "section": "exemplar",
                  "content": q, "embed_text": q, "metadata": {"label": label}}
                 for label, qs in EXEMPLARS.items() for q in qs]
    print(f"  schema cards {len(cards)}, table-to-text rows {len(rows)}, router exemplars {len(exemplars)}")
    chunks += cards + rows + exemplars

    if providers.embeddings_available():
        print(f"Embedding {len(chunks)} chunks with {config.EMBED_PROVIDER} ({config.EMBED_DIM}d) ...")
        vecs = providers.embed([c["embed_text"] for c in chunks], is_query=False)
    else:
        print(f"!! No {config.EMBED_PROVIDER} API key: indexing in BM25-only mode (no vectors). Re-run after adding the key.")
        vecs = [None] * len(chunks)

    with admin_conn() as conn:
        conn.execute(DDL)
        from pgvector.psycopg import register_vector
        register_vector(conn)
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO rag.chunks (collection, source, title, section, content, embed_text, metadata, embedding) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                [(c["collection"], c["source"], c["title"], c["section"], c["content"], c["embed_text"],
                  json.dumps(c["metadata"], default=str), v) for c, v in zip(chunks, vecs)])
        conn.execute(BUILD_TERMS)
        conn.commit()
        n = conn.execute("SELECT collection, count(*) AS n FROM rag.chunks GROUP BY 1 ORDER BY 1").fetchall()
        terms = conn.execute("SELECT count(DISTINCT term) AS n FROM rag.chunk_terms").fetchone()["n"]
    for r in n:
        print(f"  rag.chunks[{r['collection']}] = {r['n']}")
    print(f"  BM25 vocabulary: {terms} terms.  Done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
