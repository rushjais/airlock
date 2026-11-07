"""Postgres access (Supabase session pooler). All tables live in the `airlock` schema."""
import contextvars
import re
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import atexit

from . import config

_pool: ConnectionPool | None = None
# Demo sandboxes: each visitor's requests run against their own schema (set per request by airlock.demo).
SANDBOX: contextvars.ContextVar[str | None] = contextvars.ContextVar("airlock_sandbox", default=None)
SANDBOX_RE = re.compile(r"^airlock_demo_[a-f0-9]{12}$")


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            config.need("DATABASE_URL"),
            min_size=1,
            max_size=int(config.get("AIRLOCK_DB_POOL") or 8),  # API + executor + scripts must fit Supabase's 15-client session pooler
            # Supabase's pooler drops idle connections: recycle ours before it does, so a long-running process
            # (the executor) never gets a dead connection mid-payment. (No per-checkout ping: it doubled latency.)
            max_idle=60,
            max_lifetime=600,
            kwargs={"row_factory": dict_row, "prepare_threshold": None, "options": f"-c search_path={config.DB_SCHEMA},public"},
            open=True,
        )
        atexit.register(_pool.close)
    return _pool


@contextmanager
def _conn():
    """A pooled connection. Inside a demo sandbox, the search_path is set with SET LOCAL, so it lasts only for this
    checkout's transaction and the connection goes back to the pool unchanged."""
    with pool().connection() as conn:
        sb = SANDBOX.get()
        if sb:
            if not SANDBOX_RE.match(sb):
                raise ValueError("bad sandbox name")
            conn.execute(f"set local search_path to {sb},public")
        yield conn


@contextmanager
def tx():
    """One transaction. Commits on success, rolls back on exception."""
    with _conn() as conn:
        with conn.transaction():
            yield conn


def one(sql: str, *params):
    with _conn() as conn:
        return conn.execute(sql, params).fetchone()


def all_rows(sql: str, *params):
    with _conn() as conn:
        return conn.execute(sql, params).fetchall()


@contextmanager
def message_lock(key: str):
    """Serialize work on one email across threads/processes (Postgres session advisory lock)."""
    with pool().connection() as conn:
        conn.execute("select pg_advisory_lock(hashtext(%s))", (key,))
        try:
            yield
        finally:
            conn.execute("select pg_advisory_unlock(hashtext(%s))", (key,))
