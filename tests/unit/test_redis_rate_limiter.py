"""Unit tests for the Redis-backed rate limiter's own logic.

Uses a small in-process fake client (``incr``/``expire``/``ping``) rather than the real ``redis``
package, per ``.claude/rules/testing.md`` ("Unit tests isolate ... external filesystems") - there
is no live Redis in this test run, and this class is fully duck-typed so it doesn't need one to
verify its counting and fail-open behavior.
"""

import pytest
from structlog.testing import capture_logs

from policy_model_router.adapters.redis_rate_limiter import (
    BACKEND_UNAVAILABLE_TOTAL,
    RedisRateLimiter,
)


def _counter_value() -> float:
    """Read the current value of the module-level Prometheus counter."""
    (family,) = BACKEND_UNAVAILABLE_TOTAL.collect()
    value: float = family.samples[0].value
    return value


class _FakeRedisClient:
    """Minimal in-process stand-in for ``redis.asyncio.Redis``: ``incr``/``expire``/``ping``."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expirations: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        """Increment and return the counter for ``key``, starting at 1."""
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> None:
        """Record the TTL that would have been set for ``key``."""
        self.expirations[key] = seconds

    async def ping(self) -> None:
        """Succeed unconditionally; failure is simulated by a different fake below."""
        return


class _FailingClient:
    """Fake client whose every method raises, to exercise the fail-open path."""

    async def incr(self, _key: str) -> int:
        """Always raise, simulating a Redis connectivity error."""
        raise ConnectionError("redis unreachable")

    async def expire(self, _key: str, _seconds: int) -> None:
        """Always raise; unreachable in practice since ``incr`` raises first."""
        raise ConnectionError("redis unreachable")

    async def ping(self) -> None:
        """Always raise, simulating a failed startup connectivity check."""
        raise ConnectionError("redis unreachable")


@pytest.mark.anyio
async def test_allows_requests_up_to_the_limit_within_one_window() -> None:
    client = _FakeRedisClient()
    limiter = RedisRateLimiter(client, max_requests=2, window_seconds=60)

    assert await limiter.allow("key") is True
    assert await limiter.allow("key") is True


@pytest.mark.anyio
async def test_rejects_a_request_exceeding_the_limit_within_one_window() -> None:
    client = _FakeRedisClient()
    limiter = RedisRateLimiter(client, max_requests=1, window_seconds=60)

    assert await limiter.allow("key") is True
    assert await limiter.allow("key") is False


@pytest.mark.anyio
async def test_tracks_each_key_independently() -> None:
    client = _FakeRedisClient()
    limiter = RedisRateLimiter(client, max_requests=1, window_seconds=60)

    assert await limiter.allow("key-a") is True
    assert await limiter.allow("key-b") is True
    assert await limiter.allow("key-a") is False


@pytest.mark.anyio
async def test_sets_expiry_only_on_the_first_request_of_a_window() -> None:
    client = _FakeRedisClient()
    limiter = RedisRateLimiter(client, max_requests=5, window_seconds=42)

    await limiter.allow("key")
    await limiter.allow("key")

    assert len(client.expirations) == 1
    assert next(iter(client.expirations.values())) == 42


@pytest.mark.anyio
async def test_allow_fails_open_when_the_backend_is_unavailable() -> None:
    limiter = RedisRateLimiter(_FailingClient(), max_requests=1, window_seconds=60)

    assert await limiter.allow("key") is True


@pytest.mark.anyio
async def test_allow_increments_the_backend_unavailable_counter_on_failure() -> None:
    """The Prometheus counter exposed on GET /metrics tracks every fail-open (ADR-0008)."""
    limiter = RedisRateLimiter(_FailingClient(), max_requests=1, window_seconds=60)
    before = _counter_value()

    await limiter.allow("key")

    assert _counter_value() == before + 1


@pytest.mark.anyio
async def test_allow_never_logs_the_raw_key_on_failure() -> None:
    """The key embeds the caller's IP (see entrypoints/http.py); only a fingerprint is logged."""
    limiter = RedisRateLimiter(_FailingClient(), max_requests=1, window_seconds=60)
    raw_key = "203.0.113.5:credit-analysis-agent"

    with capture_logs() as logs:
        await limiter.allow(raw_key)

    assert len(logs) == 1
    assert logs[0]["event"] == "rate_limiter_backend_unavailable"
    assert "key" not in logs[0]
    assert raw_key not in repr(logs[0])
    assert logs[0]["key_fingerprint"] != raw_key


@pytest.mark.anyio
async def test_ping_raises_when_the_backend_is_unavailable() -> None:
    limiter = RedisRateLimiter(_FailingClient(), max_requests=1, window_seconds=60)

    with pytest.raises(ConnectionError):
        await limiter.ping()


@pytest.mark.anyio
async def test_ping_succeeds_when_the_backend_is_reachable() -> None:
    limiter = RedisRateLimiter(_FakeRedisClient(), max_requests=1, window_seconds=60)

    await limiter.ping()
