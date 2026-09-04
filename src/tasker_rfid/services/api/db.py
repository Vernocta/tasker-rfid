"""Database access for the API.

A small connection pool and two helpers. Route code stays readable
because the SQL is right there in the route, not hidden behind an ORM.

Every query here is read-only or writes tables the API is allowed to
write. It never writes containers.status: SPEC.md section 4 reserves that
for the state engine, and this service has no code that sets it.
"""

import os
from collections.abc import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None


def dsn() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and fill it in."
        )
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def open_pool() -> None:
    global _pool
    _pool = ConnectionPool(dsn(), min_size=1, max_size=8, open=True)


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def connection() -> Iterator[psycopg.Connection]:
    """FastAPI dependency: one pooled connection per request."""
    if _pool is None:
        raise RuntimeError("the connection pool is not open")
    with _pool.connection() as conn:
        yield conn


def fetch_all(conn: psycopg.Connection, sql: str, params: tuple = ()) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def fetch_one(conn: psycopg.Connection, sql: str, params: tuple = ()) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, params)
        return cur.fetchone()
