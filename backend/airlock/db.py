"""Postgres access (Supabase session pooler). All tables live in the `airlock` schema."""
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import atexit

from . import config

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            config.need("DATABASE_URL"),
            min_size=1,
            max_size=10,
            # Supabase's pooler drops idle connections; check each one before use and recycle idle ones, so a
            # long-running process (the executor) never gets a dead connection mid-payment.
            check=ConnectionPool.check_connection,
            max_idle=120,
            max_lifetime=1800,
            kwargs={"row_factory": dict_row, "prepare_threshold": None, "options": f"-c search_path={config.DB_SCHEMA},public"},
            open=True,
        )
        atexit.register(_pool.close)
    return _pool


@contextmanager
def tx():
    """One transaction. Commits on success, rolls back on exception."""
    with pool().connection() as conn:
        with conn.transaction():
            yield conn


def one(sql: str, *params):
    with pool().connection() as conn:
        return conn.execute(sql, params).fetchone()


def all_rows(sql: str, *params):
    with pool().connection() as conn:
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
