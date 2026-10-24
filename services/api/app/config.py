from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    ro_url: str = ""
    rw_url: str = ""
    cors_origins: list[str] = field(default_factory=lambda: ["http://localhost:3000"])
    demo_per_minute: int = 3
    demo_per_hour: int = 20
    demo_global_per_hour: int = 100
    demo_history_cap: int = 200
    max_range_days: int = 366
    # --- serverless deployment (Vercel) ---------------------------------------------------------------------------
    # "memory": per-process limiter (single container / local dev).  "postgres": shared limiter, correct across the many
    # short-lived instances a serverless platform starts. Requires rate_limit_salt.
    rate_limit_backend: str = "memory"
    # Trust X-Forwarded-For for the client address. ONLY enable behind a proxy that overwrites the header (Vercel does).
    trust_proxy_headers: bool = False
    # Secret salt for hashing client addresses; raw IPs are never stored.
    rate_limit_salt: str = ""
    ro_pool_max: int = 5
    rw_pool_max: int = 2

    @classmethod
    def from_env(cls) -> Settings:
        pool = _int("DB_POOL_MAX", 5)
        return cls(
            ro_url=os.environ.get("DATABASE_URL_RO", ""),
            rw_url=os.environ.get("DATABASE_URL_RW", ""),
            cors_origins=[o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()],
            demo_per_minute=_int("DEMO_RUNS_PER_MINUTE", 3),
            demo_per_hour=_int("DEMO_RUNS_PER_HOUR", 20),
            rate_limit_backend=os.environ.get("RATE_LIMIT_BACKEND", "memory"),
            trust_proxy_headers=os.environ.get("TRUST_PROXY_HEADERS", "0") == "1",
            rate_limit_salt=os.environ.get("RATE_LIMIT_SALT", ""),
            ro_pool_max=pool,
            rw_pool_max=max(1, min(2, pool)),
        )
