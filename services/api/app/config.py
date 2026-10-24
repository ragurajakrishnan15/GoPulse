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

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            ro_url=os.environ.get("DATABASE_URL_RO", ""),
            rw_url=os.environ.get("DATABASE_URL_RW", ""),
            cors_origins=[o.strip() for o in os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()],
            demo_per_minute=_int("DEMO_RUNS_PER_MINUTE", 3),
            demo_per_hour=_int("DEMO_RUNS_PER_HOUR", 20),
        )
