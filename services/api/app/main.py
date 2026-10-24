"""GoPulse read API (FastAPI).

    uvicorn services.api.app.main:app --env-file .env        (from the repo root)

All data is synthetic. Dashboard queries use a read-only Postgres role; the demo-run endpoint uses a second role that
can only INSERT run-history rows.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import psycopg.errors
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from services.api.app.config import Settings
from services.api.app.db import make_pool
from services.api.app.ratelimit import RateLimiter
from services.api.app.routers import analytics, demo, ops

log = logging.getLogger("gopulse.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.ro_pool = make_pool(settings.ro_url) if settings.ro_url else None
        app.state.rw_pool = make_pool(settings.rw_url, max_size=2) if settings.rw_url else None
        app.state.limiter = RateLimiter(settings.demo_per_minute, settings.demo_per_hour, settings.demo_global_per_hour)
        yield
        for p in (app.state.ro_pool, app.state.rw_pool):
            if p is not None:
                p.close()

    app = FastAPI(title="GoPulse API", version="1.0.0", lifespan=lifespan,
                  description="Read API for the GoPulse AI product analytics demo. **All data is synthetic.**")
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type"], expose_headers=["Retry-After"], max_age=600)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Cache-Control", "no-store" if request.method != "GET" else "public, max-age=30")
        return resp

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException):
        return JSONResponse({"error": {"code": exc.status_code, "message": exc.detail}}, status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(x) for x in e["loc"] if x != "query"), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse({"error": {"code": 422, "message": "Invalid request parameters", "details": details}}, status_code=422)

    @app.exception_handler(psycopg.errors.UndefinedTable)
    async def not_published(_: Request, exc: Exception):
        # A fresh deployment has no `serving` schema until the first publish. Say so instead of a generic 500.
        return JSONResponse({"error": {"code": 503, "message": "No data has been published yet. Run the seed/publish job."}},
                            status_code=503, headers={"Retry-After": "30"})

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.exception("unhandled error", exc_info=exc)
        return JSONResponse({"error": {"code": 500, "message": "Internal server error"}}, status_code=500)

    @app.get("/health", tags=["health"], summary="Basic service health")
    def health(request: Request):
        db = "not_configured"
        pool = request.app.state.ro_pool
        if pool is not None:
            try:
                with pool.connection() as conn:
                    conn.execute("SELECT 1")
                db = "ok"
            except Exception:
                db = "unavailable"
        body = {"status": "ok" if db in ("ok", "not_configured") else "degraded", "database": db, "synthetic_data": True}
        return JSONResponse(body, status_code=200 if body["status"] == "ok" else 503)

    for r in (analytics.router, ops.router, demo.router):
        app.include_router(r)
    return app


app = create_app()
