"""GoPulse read API (FastAPI).

    uvicorn services.api.app.main:app --env-file .env        (from the repo root)

All data is synthetic. Dashboard queries use a read-only Postgres role; the demo-run endpoint uses a second role that
can only INSERT run-history rows.
"""
from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager

import psycopg.errors
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from services.api.app.config import Settings
from services.api.app.db import Pools
from services.api.app.ratelimit import PgRateLimiter, RateLimiter
from services.api.app.routers import analytics, demo, ops

log = logging.getLogger("gopulse.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    if settings.rate_limit_backend not in ("memory", "postgres"):
        raise RuntimeError(f"RATE_LIMIT_BACKEND must be 'memory' or 'postgres', got {settings.rate_limit_backend!r}")
    if settings.rate_limit_backend == "postgres" and not settings.rate_limit_salt:
        raise RuntimeError("RATE_LIMIT_SALT is required with RATE_LIMIT_BACKEND=postgres (client addresses are stored only as salted hashes)")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        app.state.pools.close()  # (shutdown only: serverless runtimes may never call this, which is fine)

    app = FastAPI(title="GoPulse API", version="1.0.0", lifespan=lifespan,
                  description="Read API for the GoPulse AI product analytics demo. **All data is synthetic.**")
    # State is set here, not in the lifespan hook: serverless runtimes may not run lifespan events, and the pools connect lazily.
    app.state.settings = settings
    app.state.pools = Pools(settings)
    limiter_cls = PgRateLimiter if settings.rate_limit_backend == "postgres" else RateLimiter
    app.state.limiter = limiter_cls(settings.demo_per_minute, settings.demo_per_hour, settings.demo_global_per_hour)
    app.state.rate_limit_salt = settings.rate_limit_salt or secrets.token_hex(16)  # per-process salt when none is configured
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
        pool = request.app.state.pools.ro
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
