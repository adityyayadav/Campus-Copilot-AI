"""PostgreSQL helpers: one database holds both the relational tables (schema public)
and the vector/BM25 index (schema rag)."""
from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row

from . import config


@contextmanager
def admin_conn():
    """Full-privilege connection used for ingestion and retrieval over the rag schema."""
    with psycopg.connect(config.DATABASE_URL, row_factory=dict_row) as conn:
        try:
            register_vector(conn)
        except psycopg.ProgrammingError:
            conn.rollback()  # extension not created yet (first ingest creates it)
        yield conn


@contextmanager
def reader_conn():
    """Read-only role used ONLY by the Text-to-SQL agent (SELECT on schema public, no rag access)."""
    with psycopg.connect(config.reader_url(), row_factory=dict_row) as conn:
        conn.read_only = True
        yield conn


def run_readonly_query(sql: str, student_id: str | None = None, role: str | None = None,
                       timeout_ms: int = 5000) -> tuple[list[str], list[dict]]:
    """Executes as the read-only role. Row-level security on fee_ledger / grant_applications uses these settings:
    role 'admin' sees every row, a student sees only their own rows, a guest (None) sees no personal rows."""
    with reader_conn() as conn:
        with conn.transaction():
            conn.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
            conn.execute("SELECT set_config('app.student_id', %s, true)", (student_id or "",))
            conn.execute("SELECT set_config('app.role', %s, true)", (role or "",))
            cur = conn.execute(sql)
            cols = [d.name for d in cur.description] if cur.description else []
            rows = cur.fetchall() if cur.description else []
    return cols, rows
