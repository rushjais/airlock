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
            kwargs={"row_factory": dict_row, "options": "-c search_path=airlock,public"},
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
