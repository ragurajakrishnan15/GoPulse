"""Rate limiters for the demo-run endpoint.

* RateLimiter     in-memory sliding window. Correct for ONE process (local dev, a single container).
* PgRateLimiter   state lives in PostgreSQL (`serving.demo_hits`), so limits hold across the many short-lived instances a
                  serverless platform runs. Clients are stored only as salted hashes, never as IP addresses.
"""
from __future__ import annotations

import hashlib
import math
import threading
import time
from collections import defaultdict, deque
from typing import Any

# Postgres advisory-lock keys (arbitrary, just distinct and stable)
LOCK_RATE_LIMIT = 7101
LOCK_DEMO_RUN = 7102


class RateLimiter:
    def __init__(self, per_minute: int, per_hour: int, global_per_hour: int):
        self.per_minute, self.per_hour, self.global_per_hour = per_minute, per_hour, global_per_hour
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._global: deque[float] = deque()
        self._lock = threading.Lock()

    def check(self, key: str, now: float | None = None) -> int | None:
        """Record a hit. Returns None if allowed, else the seconds to wait (Retry-After)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > 3600:
                q.popleft()
            while self._global and now - self._global[0] > 3600:
                self._global.popleft()
            last_min = [t for t in q if now - t <= 60]
            if len(last_min) >= self.per_minute:
                return max(1, int(60 - (now - last_min[0])) + 1)
            if len(q) >= self.per_hour:
                return max(1, int(3600 - (now - q[0])) + 1)
            if len(self._global) >= self.global_per_hour:
                return max(1, int(3600 - (now - self._global[0])) + 1)
            q.append(now)
            self._global.append(now)
            return None


class PgRateLimiter:
    """Shared sliding-window limiter. Call `check` inside a transaction on the read-write connection and commit it BEFORE
    doing the expensive work, so a request that later fails still counts against the quota."""

    def __init__(self, per_minute: int, per_hour: int, global_per_hour: int):
        self.per_minute, self.per_hour, self.global_per_hour = per_minute, per_hour, global_per_hour

    def check(self, conn: Any, key: str) -> int | None:
        # Serialise concurrent checks so two instances cannot both slip under the limit.
        conn.execute("SELECT pg_advisory_xact_lock(%s)", [LOCK_RATE_LIMIT])
        conn.execute("DELETE FROM serving.demo_hits WHERE at < now() - interval '1 hour'")
        r = conn.execute(
            """SELECT count(*) FILTER (WHERE client_hash = %(k)s AND at > now() - interval '1 minute') AS in_min,
                      extract(epoch FROM (min(at) FILTER (WHERE client_hash = %(k)s AND at > now() - interval '1 minute')
                                          + interval '1 minute' - now())) AS min_wait,
                      count(*) FILTER (WHERE client_hash = %(k)s) AS in_hour,
                      extract(epoch FROM (min(at) FILTER (WHERE client_hash = %(k)s) + interval '1 hour' - now())) AS hour_wait,
                      count(*) AS total,
                      extract(epoch FROM (min(at) + interval '1 hour' - now())) AS total_wait
               FROM serving.demo_hits""", {"k": key}).fetchone()
        for count, limit, wait in ((r["in_min"], self.per_minute, r["min_wait"]),
                                   (r["in_hour"], self.per_hour, r["hour_wait"]),
                                   (r["total"], self.global_per_hour, r["total_wait"])):
            if count >= limit:
                return max(1, math.ceil(wait or 1))
        conn.execute("INSERT INTO serving.demo_hits (client_hash) VALUES (%s)", [key])
        return None


def client_key(request: Any, trust_proxy_headers: bool, salt: str) -> str:
    """Salted hash identifying the caller. The raw address is used for hashing only and is never stored or logged."""
    ip = request.client.host if request.client else "unknown"
    if trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            ip = forwarded.split(",")[0].strip() or ip
    return hashlib.sha256(f"{salt}|{ip}".encode()).hexdigest()[:32]
