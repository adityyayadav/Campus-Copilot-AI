"""PostgreSQL helpers: one database holds both the relational tables (schema public)
and the vector/BM25 index (schema rag).

Connections come from two bounded pools (created lazily, shared by all request threads):
  * admin pool  - retrieval over the rag schema, cache, telemetry
  * reader pool - the read-only role used ONLY by the Text-to-SQL agent
Per-request security settings (app.role / app.student_id, statement_timeout) are transaction-local
(SET LOCAL / set_config(..., true)), so nothing leaks between requests that reuse a pooled connection.
"""
import atexit
import threading
from contextlib import contextmanager

import psycopg
from pgvector.psycopg import register_vector
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from . import config

_lock = threading.Lock()
_pools: dict[str, ConnectionPool] = {}


@atexit.register
def _close_pools() -> None:  # clean shutdown for scripts and server workers
    for p in list(_pools.values()):
        try:
            p.close(timeout=2)
        except Exception:  # noqa: BLE001
            pass


def _configure_admin(conn: psycopg.Connection) -> None:
    try:
        register_vector(conn)
    except psycopg.ProgrammingError:
        pass  # vector extension not created yet (first ingest creates it)
    conn.rollback()


def _configure_reader(conn: psycopg.Connection) -> None:
    conn.read_only = True


def _pool(name: str) -> ConnectionPool:
    pool = _pools.get(name)
    if pool is None:
        with _lock:
            pool = _pools.get(name)
            if pool is None:
                url = config.DATABASE_URL if name == "admin" else config.reader_url()
                pool = ConnectionPool(
                    url, min_size=1, max_size=config.DB_POOL_MAX, timeout=config.DB_POOL_TIMEOUT_S,
                    kwargs={"row_factory": dict_row},
                    configure=_configure_admin if name == "admin" else _configure_reader,
                    name=f"campus-{name}", open=True,
                )
                _pools[name] = pool
    return pool


@contextmanager
def admin_conn():
    """Full-privilege pooled connection for retrieval over the rag schema, cache and telemetry.
    Commits on normal exit, rolls back on error."""
    with _pool("admin").connection() as conn:
        yield conn


@contextmanager
def reader_conn():
    """Pooled read-only connection used ONLY by the Text-to-SQL agent (SELECT on schema public, no rag access)."""
    with _pool("reader").connection() as conn:
        yield conn


def pool_stats() -> dict:
    return {name: {k: v for k, v in p.get_stats().items()
                   if k in ("pool_size", "pool_available", "requests_waiting", "requests_num", "connections_num")}
            for name, p in _pools.items()}


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
