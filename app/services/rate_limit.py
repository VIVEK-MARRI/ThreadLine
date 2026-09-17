"""Process-local sliding-window rate limiter (Stage 34, Part F).

Single-node cost protection for expensive endpoints (POST /api/v1/query
and POST /api/v1/query/evidence).  Deliberately NOT distributed: with one
uvicorn process the in-memory window is exact; behind multiple processes
each enforces its own window (documented in DEPLOYMENT.md — this is
overload protection, not a security boundary).

Design
------
* Sliding window with per-key timestamp deques; expired entries pruned on
  every check so memory stays proportional to recent traffic.
* Bounded key space: at most ``max_keys`` principals are tracked; beyond
  that the least-recently-seen key is evicted (its quota restarts — fail
  open on eviction, never fail closed).
* Thread-safe via one lock (uvicorn serves requests on threads).
* Deterministic: same call sequence → same allow/deny decisions; the clock
  is injectable for tests.
* Privacy: keys are opaque principal strings (``user:<uuid>`` /
  ``ip:<host>``).  The limiter NEVER logs keys, tokens, or request bodies.
"""

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Callable

logger = logging.getLogger(__name__)


def _default_clock() -> datetime:
    return datetime.now(timezone.utc)


class SlidingWindowRateLimiter:
    """Fixed principal quota: at most ``max_requests`` check() calls per
    ``window_seconds`` rolling window, per key."""

    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        max_keys: int = 10000,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_requests <= 0:
            raise ValueError("max_requests must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if max_keys <= 0:
            raise ValueError("max_keys must be positive")
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._max_keys = max_keys
        self._clock = clock or _default_clock
        self._lock = threading.Lock()
        self._hits: dict[str, deque] = {}

    @property
    def max_requests(self) -> int:
        return self._max_requests

    @property
    def window_seconds(self) -> float:
        return self._window_seconds

    def key_count(self) -> int:
        """Number of principals currently tracked (bounded by max_keys)."""
        with self._lock:
            return len(self._hits)

    def check(self, key: str, now: datetime | None = None) -> tuple[bool, float]:
        """Record one request for ``key``.

        Returns (allowed, retry_after_seconds).  When denied,
        retry_after_seconds is the seconds until the oldest in-window hit
        expires (always > 0); when allowed it is 0.0.
        """
        moment = now or self._clock()
        cutoff = moment.timestamp() - self._window_seconds
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                if len(self._hits) >= self._max_keys:
                    # Bounded memory: evict the least-recently-seen key.
                    # Its quota restarts (fail open, never fail closed).
                    oldest_key = min(
                        self._hits,
                        key=lambda k: self._hits[k][-1] if self._hits[k] else float("inf"),
                    )
                    del self._hits[oldest_key]
                hits = self._hits[key] = deque()
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= self._max_requests:
                retry_after = max(0.0, hits[0] - cutoff)
                # Guard against float dust: a denied caller must wait.
                return False, max(retry_after, 0.001)
            hits.append(moment.timestamp())
            return True, 0.0

    def reset(self) -> None:
        """Clear all tracked state (tests only)."""
        with self._lock:
            self._hits.clear()
