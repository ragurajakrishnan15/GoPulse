"""Small in-memory sliding-window limiter for the demo-run endpoint (single-process; see docs for multi-instance)."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


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
