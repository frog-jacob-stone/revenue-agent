"""Dual-bucket rate limiter for the Harvest API.

Harvest enforces two independent limits:

  - general endpoints : 100 requests / 15 seconds
  - ``/v2/reports/*`` : 100 requests / 15 **minutes**

The reports limit is the binding one, which is why the invoicing pre-flight is
built on `/time_entries` and `/expenses` rather than `/reports/*`. The two
buckets are kept separate so a future reports caller cannot silently consume
the general budget (or vice versa).

The clock is injectable so the refill maths can be unit-tested without any
real sleeping — see `tests/test_harvest_limiter.py`.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

# (capacity, refill window in seconds)
GENERAL_LIMIT = (100, 15.0)
REPORTS_LIMIT = (100, 900.0)

Clock = Callable[[], float]
Sleeper = Callable[[float], Awaitable[None]]


@dataclass
class _Bucket:
    """A token bucket that refills continuously over `window` seconds."""

    capacity: int
    window: float
    tokens: float
    updated_at: float

    def refill(self, now: float) -> None:
        elapsed = now - self.updated_at
        if elapsed <= 0:
            return
        self.tokens = min(self.capacity, self.tokens + elapsed * (self.capacity / self.window))
        self.updated_at = now

    def time_until_token(self, now: float) -> float:
        """Seconds to wait before one token is available. 0 when ready now."""
        self.refill(now)
        if self.tokens >= 1:
            return 0.0
        deficit = 1 - self.tokens
        return deficit / (self.capacity / self.window)


class HarvestRateLimiter:
    """Async token-bucket limiter with separate general and reports budgets."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        sleeper: Sleeper | None = None,
    ) -> None:
        import time

        self._clock: Clock = clock or time.monotonic
        self._sleep: Sleeper = sleeper or asyncio.sleep
        self._lock = asyncio.Lock()
        now = self._clock()
        self._buckets = {
            "general": _Bucket(GENERAL_LIMIT[0], GENERAL_LIMIT[1], float(GENERAL_LIMIT[0]), now),
            "reports": _Bucket(REPORTS_LIMIT[0], REPORTS_LIMIT[1], float(REPORTS_LIMIT[0]), now),
        }

    @staticmethod
    def bucket_for(path: str) -> str:
        return "reports" if path.startswith("/reports") else "general"

    async def acquire(self, path: str) -> None:
        """Block until a token is available for this path's bucket, then spend it."""
        name = self.bucket_for(path)
        bucket = self._buckets[name]
        while True:
            async with self._lock:
                wait = bucket.time_until_token(self._clock())
                if wait <= 0:
                    bucket.tokens -= 1
                    return
            logger.debug("harvest limiter: %s bucket exhausted, waiting %.2fs", name, wait)
            await self._sleep(wait)

    async def penalize(self, path: str, retry_after: float) -> None:
        """Honor a 429 `Retry-After` by draining the bucket for that long.

        Zeroing the tokens and rewinding `updated_at` means every concurrent
        caller on this bucket waits out the penalty, not just the one that
        got the 429.
        """
        bucket = self._buckets[self.bucket_for(path)]
        async with self._lock:
            now = self._clock()
            bucket.tokens = 0.0
            # Rewind so a full `retry_after` must elapse before one token exists.
            bucket.updated_at = now + retry_after - (bucket.window / bucket.capacity)
        logger.warning("harvest limiter: 429 on %s, backing off %.1fs", path, retry_after)
        await self._sleep(retry_after)


# Module-level default used by app.integrations.harvest. Tests construct their
# own instance with a fake clock rather than mutating this one.
limiter = HarvestRateLimiter()
