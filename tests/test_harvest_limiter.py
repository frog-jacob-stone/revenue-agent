"""Rate limiter unit tests — driven by a fake clock, so nothing really sleeps."""
from __future__ import annotations

import pytest

from app.integrations.harvest_limiter import HarvestRateLimiter


class FakeClock:
    """Monotonic clock the test advances by hand. `sleep` jumps it forward."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def limiter(clock: FakeClock) -> HarvestRateLimiter:
    return HarvestRateLimiter(clock=clock, sleeper=clock.sleep)


def test_buckets_are_routed_by_path() -> None:
    assert HarvestRateLimiter.bucket_for("/reports/time/tasks") == "reports"
    assert HarvestRateLimiter.bucket_for("/time_entries") == "general"
    assert HarvestRateLimiter.bucket_for("/invoices") == "general"


async def test_burst_up_to_capacity_does_not_sleep(limiter, clock) -> None:
    for _ in range(100):
        await limiter.acquire("/time_entries")
    assert clock.slept == []


async def test_exhausted_bucket_waits_for_refill(limiter, clock) -> None:
    for _ in range(100):
        await limiter.acquire("/time_entries")

    await limiter.acquire("/time_entries")

    assert len(clock.slept) == 1
    # 100 tokens per 15s → one token every 0.15s.
    assert clock.slept[0] == pytest.approx(0.15, abs=1e-6)


async def test_general_and_reports_budgets_are_independent(limiter, clock) -> None:
    for _ in range(100):
        await limiter.acquire("/time_entries")

    # The reports bucket is untouched, so this must not block.
    await limiter.acquire("/reports/time/tasks")
    assert clock.slept == []


async def test_reports_bucket_refills_far_more_slowly(limiter, clock) -> None:
    for _ in range(100):
        await limiter.acquire("/reports/time/tasks")

    await limiter.acquire("/reports/time/tasks")

    # 100 per 15 minutes → one token every 9s.
    assert clock.slept[0] == pytest.approx(9.0, abs=1e-6)


async def test_penalize_honors_retry_after_for_the_whole_bucket(limiter, clock) -> None:
    await limiter.acquire("/invoices")

    await limiter.penalize("/invoices", 12.0)

    assert clock.slept == [12.0]
    clock.slept.clear()

    # penalize() already slept out Retry-After, so the caller's retry must go
    # through immediately — sleeping again would double-charge one 429.
    await limiter.acquire("/invoices")
    assert clock.slept == []

    # But only that one: the bucket was drained, so tokens banked before the
    # 429 are gone and a second request waits for a fresh refill.
    await limiter.acquire("/invoices")
    assert clock.slept == [pytest.approx(0.15, abs=1e-6)]


async def test_penalize_does_not_touch_the_other_bucket(limiter, clock) -> None:
    await limiter.penalize("/invoices", 12.0)
    clock.slept.clear()

    await limiter.acquire("/reports/time/tasks")
    assert clock.slept == []
