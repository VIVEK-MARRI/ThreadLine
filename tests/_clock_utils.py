"""Shared controllable clock for deterministic lease-expiry tests.

Underscore-prefixed so pytest does not collect this module as a test.
Both the repository and the worker accept a `clock: Callable[[], datetime]`,
so tests inject one `MutableClock` instance everywhere it is needed.
"""

from datetime import datetime, timedelta, timezone


class MutableClock:
    def __init__(self, initial: datetime | None = None) -> None:
        self._now = initial or datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self._now

    def advance(self, **kwargs) -> datetime:
        self._now = self._now + timedelta(**kwargs)
        return self._now

    def set(self, value: datetime) -> datetime:
        self._now = value
        return self._now