"""Unit tests for the Redis-backed rate limiter's own logic.

Uses a small in-process fake client (``eval``/``ping``) rather than the real ``redis``
package, per ``.claude/rules/testing.md`` ("Unit tests isolate ... external filesystems") - there
is no live Redis in this test run, and this class is fully duck-typed so it doesn't need one to
verify its counting and fail-open behavior.
"""

import pytest
from structlog.testing import capture_logs

from policy_model_router.adapters.redis_rate_limiter import (
    _KEY_PREFIX,
    BACKEND_UNAVAILABLE_TOTAL,
    RedisRateLimiter,
)


def _counter_value() -> float:
    """Read the current value of the module-level Prometheus counter."""
    (family,) = BACKEND_UNAVAILABLE_TOTAL.collect()
    value: float = family.samples[0].value
    return value


class _FakeRedisClient:
    """Minimal in-process stand-in for ``redis.asyncio.Redis``: ``eval``/``ping``.

    Emulates the ``INCR`` + conditional ``EXPIRE`` semantics of ``_INCR_AND_EXPIRE_SCRIPT`` in
    Python rather than a real Lua interpreter; the real script is exercised end-to-end against a
    live Redis by ``tests/integration/test_redis_rate_limiter_integration.py``.
    """

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expirations: dict[str, int] = {}
        self._has_ttl: set[str] = set()
        self.closed = False

    async def eval(self, _script: str, _numkeys: int, key: str, window_ms: int) -> int:
        """Increment ``key`` and set its TTL (in milliseconds) if this is the first hit or the
        TTL is missing.
        """
        self.counts[key] = self.counts.get(key, 0) + 1
        if key not in self._has_ttl:
            self.expirations[key] = window_ms
            self._has_ttl.add(key)
        return self.counts[key]

    async def ping(self) -> None:
        """Succeed unconditionally; failure is simulated by a different fake below."""
        return

    async def aclose(self) -> None:
        """Record that the connection was released."""
        self.closed = True

    def drop_ttl(self, key: str) -> None:
        """Simulate a crash between INCR and EXPIRE by forgetting ``key``'s TTL bookkeeping."""
        self.expirations.pop(key, None)
        self._has_ttl.discard(key)


class _FailingClient:
    """Fake client whose every method raises, to exercise the fail-open path."""

    async def eval(self, _script: str, _numkeys: int, _key: str, _window_seconds: int) -> int:
        """Always raise, simulating a Redis connectivity error."""
        raise ConnectionError("redis unreachable")

    async def ping(self) -> None:
        """Always raise, simulating a failed startup connectivity check."""
        raise ConnectionError("redis unreachable")

    async def aclose(self) -> None:
        """Always raise, simulating a failure while releasing the connection."""
        raise ConnectionError("redis unreachable")


@pytest.mark.parametrize("window_seconds", [0, -1, float("inf"), 86_400.0001, 1e308])
def test_rejects_an_unsafe_window_before_redis_arithmetic(window_seconds: float) -> None:
    with pytest.raises(ValueError, match="window_seconds"):
        RedisRateLimiter(_FakeRedisClient(), max_requests=1, window_seconds=window_seconds)


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
    assert next(iter(client.expirations.values())) == 42_000


@pytest.mark.anyio
async def test_repairs_a_missing_ttl_on_a_key_that_already_has_hits() -> None:
    """A crash between INCR and EXPIRE must not leave a key permanently without a TTL."""
    client = _FakeRedisClient()
    limiter = RedisRateLimiter(client, max_requests=5, window_seconds=42)
    full_key = f"{_KEY_PREFIX}:key"

    await limiter.allow("key")
    client.drop_ttl(full_key)

    await limiter.allow("key")

    assert client.expirations[full_key] == 42_000


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
async def test_fingerprint_is_stable_for_a_fixed_configured_secret() -> None:
    """A configured secret makes the fingerprint reproducible across limiter instances."""
    raw_key = "203.0.113.5:credit-analysis-agent"
    limiter_a = RedisRateLimiter(
        _FailingClient(), max_requests=1, window_seconds=60, fingerprint_secret=b"shared-secret"
    )
    limiter_b = RedisRateLimiter(
        _FailingClient(), max_requests=1, window_seconds=60, fingerprint_secret=b"shared-secret"
    )

    with capture_logs() as logs_a:
        await limiter_a.allow(raw_key)
    with capture_logs() as logs_b:
        await limiter_b.allow(raw_key)

    assert logs_a[0]["key_fingerprint"] == logs_b[0]["key_fingerprint"]


@pytest.mark.anyio
async def test_fingerprint_differs_between_different_configured_secrets() -> None:
    """Without a shared secret, an attacker with only log access cannot match fingerprints."""
    raw_key = "203.0.113.5:credit-analysis-agent"
    limiter_a = RedisRateLimiter(
        _FailingClient(), max_requests=1, window_seconds=60, fingerprint_secret=b"secret-a"
    )
    limiter_b = RedisRateLimiter(
        _FailingClient(), max_requests=1, window_seconds=60, fingerprint_secret=b"secret-b"
    )

    with capture_logs() as logs_a:
        await limiter_a.allow(raw_key)
    with capture_logs() as logs_b:
        await limiter_b.allow(raw_key)

    assert logs_a[0]["key_fingerprint"] != logs_b[0]["key_fingerprint"]


@pytest.mark.anyio
async def test_fingerprint_defaults_to_a_random_secret_when_none_is_configured() -> None:
    """Two limiter instances with no configured secret must not produce matching fingerprints."""
    raw_key = "203.0.113.5:credit-analysis-agent"
    limiter_a = RedisRateLimiter(_FailingClient(), max_requests=1, window_seconds=60)
    limiter_b = RedisRateLimiter(_FailingClient(), max_requests=1, window_seconds=60)

    with capture_logs() as logs_a:
        await limiter_a.allow(raw_key)
    with capture_logs() as logs_b:
        await limiter_b.allow(raw_key)

    assert logs_a[0]["key_fingerprint"] != logs_b[0]["key_fingerprint"]


@pytest.mark.anyio
async def test_ping_raises_when_the_backend_is_unavailable() -> None:
    limiter = RedisRateLimiter(_FailingClient(), max_requests=1, window_seconds=60)

    with pytest.raises(ConnectionError):
        await limiter.ping()


@pytest.mark.anyio
async def test_ping_succeeds_when_the_backend_is_reachable() -> None:
    limiter = RedisRateLimiter(_FakeRedisClient(), max_requests=1, window_seconds=60)

    await limiter.ping()


@pytest.mark.anyio
async def test_close_releases_the_underlying_connection() -> None:
    client = _FakeRedisClient()
    limiter = RedisRateLimiter(client, max_requests=1, window_seconds=60)

    await limiter.close()

    assert client.closed is True
