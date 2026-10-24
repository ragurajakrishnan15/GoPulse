from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from fastapi import Request
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def make_pool(url: str, *, max_size: int = 5) -> ConnectionPool:
    return ConnectionPool(url, min_size=1, max_size=max_size, kwargs={"row_factory": dict_row}, open=True, timeout=5)


def ro_conn(request: Request) -> Iterator[Connection]:
    """Read-only role: every dashboard query uses this."""
    with request.app.state.ro_pool.connection() as conn:
        yield conn


def query(conn: Connection, sql: str, params: list[Any] | tuple | None = None) -> list[dict]:
    return conn.execute(sql, params or []).fetchall()


def one(conn: Connection, sql: str, params: list[Any] | tuple | None = None) -> dict | None:
    return conn.execute(sql, params or []).fetchone()


def meta(conn: Connection) -> dict:
    m = one(conn, "SELECT published_at, as_of_date, source_run_id FROM serving.publish_meta WHERE id = 1")
    if not m:
        return {"synthetic_data": True, "last_refreshed": None, "as_of_date": None,
                "notice": "No data has been published yet."}
    return {"synthetic_data": True, "last_refreshed": m["published_at"], "as_of_date": m["as_of_date"],
            "source_run_id": m["source_run_id"]}
