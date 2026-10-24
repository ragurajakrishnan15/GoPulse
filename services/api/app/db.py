from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Any

from fastapi import HTTPException, Request
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from services.api.app.config import Settings


def make_pool(url: str, *, max_size: int = 5) -> ConnectionPool:
    """Pool that is safe behind a transaction-mode connection pooler (Supabase/pgbouncer) and on serverless:

    * prepare_threshold=None  - no server-side prepared statements (a transaction pooler hands each statement to a different
      server connection, so they break);
    * min_size=0              - hold no idle connections while an instance is frozen or idle;
    * check=check_connection  - validate a connection when it is handed out (poolers close idle ones).
    """
    return ConnectionPool(url, min_size=0, max_size=max_size, kwargs={"row_factory": dict_row, "prepare_threshold": None},
                          check=ConnectionPool.check_connection, open=True, timeout=8)


class Pools:
    """Lazily-created connection pools.

    They are created on first use, not in a startup hook: serverless runtimes may not run ASGI lifespan events, and a
    cold start should not pay for (or fail on) a database connection that this request might not need.
    """

    def __init__(self, settings: Settings):
        self._settings = settings
        self._ro: ConnectionPool | None = None
        self._rw: ConnectionPool | None = None
        self._lock = threading.Lock()

    @property
    def ro(self) -> ConnectionPool | None:
        if self._ro is None and self._settings.ro_url:
            with self._lock:
                if self._ro is None:
                    self._ro = make_pool(self._settings.ro_url, max_size=self._settings.ro_pool_max)
        return self._ro

    @property
    def rw(self) -> ConnectionPool | None:
        if self._rw is None and self._settings.rw_url:
            with self._lock:
                if self._rw is None:
                    self._rw = make_pool(self._settings.rw_url, max_size=self._settings.rw_pool_max)
        return self._rw

    def close(self) -> None:
        for p in (self._ro, self._rw):
            if p is not None:
                p.close()


def ro_conn(request: Request) -> Iterator[Connection]:
    """Read-only role: every dashboard query uses this."""
    pool = request.app.state.pools.ro
    if pool is None:
        raise HTTPException(503, "The database is not configured.")
    with pool.connection() as conn:
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
