"""Unit tests for the in-memory, fixed-window rate limiter.

Uses an injectable time source instead of real sleeps, per ``.claude/rules/testing.md`` ("Avoid
sleep-based synchronization ... in tests").
"""

import pytest

from policy_model_router.adapters.rate_limiter import InMemoryRateLimiter


class _FakeClock:
    """Deterministic monotonic-time stub, advanced explicitly by each test."""

    def __init__(self, start: float = 0.0) -> None:
        """Start the fake clock at ``start`` seconds."""
        self._now = start

    def __call__(self) -> float:
        """Return the current fake time."""
        return self._now

    def advance(self, seconds: float) -> None:
        """Move the fake clock forward by ``seconds``."""
        self._now += seconds


@pytest.mark.anyio
async def test_allows_requests_up_to_the_limit_within_one_window() -> None:
    clock = _FakeClock()
    limiter = InMemoryRateLimiter(max_requests=2, window_seconds=60, time_source=clock)

    assert await limiter.allow("key") is True
    assert await limiter.allow("key") is True


@pytest.mark.anyio
async def test_rejects_a_request_exceeding_the_limit_within_one_window() -> None:
    clock = _FakeClock()
    limiter = InMemoryRateLimiter(max_requests=1, window_seconds=60, time_source=clock)

    assert await limiter.allow("key") is True
    assert await limiter.allow("key") is False


@pytest.mark.anyio
async def test_resets_the_count_after_the_window_elapses() -> None:
    clock = _FakeClock()
    limiter = InMemoryRateLimiter(max_requests=1, window_seconds=60, time_source=clock)

    assert await limiter.allow("key") is True
    clock.advance(61)
    assert await limiter.allow("key") is True


@pytest.mark.anyio
async def test_tracks_each_key_independently() -> None:
    clock = _FakeClock()
    limiter = InMemoryRateLimiter(max_requests=1, window_seconds=60, time_source=clock)

    assert await limiter.allow("key-a") is True
    assert await limiter.allow("key-b") is True
    assert await limiter.allow("key-a") is False


@pytest.mark.anyio
async def test_ping_is_a_no_op() -> None:
    limiter = InMemoryRateLimiter(max_requests=1, window_seconds=60)

    await limiter.ping()
