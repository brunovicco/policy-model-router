"""Unit tests for ``entrypoints/http.py``'s rate limiter factory (``_build_rate_limiter``).

Injects a fake ``redis``/``redis.asyncio`` module tree via ``sys.modules`` rather than requiring
the real optional ``redis`` package.
"""

import sys
import types
from typing import Any

import pytest

from policy_model_router.adapters.rate_limiter import InMemoryRateLimiter
from policy_model_router.adapters.redis_rate_limiter import RedisRateLimiter
from policy_model_router.entrypoints.http import _build_rate_limiter


class _FakeRedisClient:
    """Stand-in for ``redis.asyncio.Redis`` that only records how it was constructed."""

    def __init__(self, url: str, **kwargs: Any) -> None:
        self.url = url
        self.kwargs = kwargs

    @classmethod
    def from_url(cls, url: str, **kwargs: Any) -> "_FakeRedisClient":
        """Mirror ``redis.asyncio.Redis.from_url``'s constructor-from-URL signature."""
        return cls(url, **kwargs)


def _install_fake_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Inject a fake ``redis``/``redis.asyncio`` module tree so no real package is needed."""
    fake_redis_module = types.ModuleType("redis")
    fake_asyncio_module = types.ModuleType("redis.asyncio")
    fake_asyncio_module.Redis = _FakeRedisClient  # type: ignore[attr-defined]
    fake_redis_module.asyncio = fake_asyncio_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "redis", fake_redis_module)
    monkeypatch.setitem(sys.modules, "redis.asyncio", fake_asyncio_module)


def test_build_rate_limiter_defaults_to_in_memory_when_redis_url_is_unset() -> None:
    limiter = _build_rate_limiter(
        max_requests=10, window_seconds=60, max_tracked_keys=100_000, redis_url=None
    )

    assert isinstance(limiter, InMemoryRateLimiter)


def test_build_rate_limiter_threads_max_tracked_keys_into_the_in_memory_limiter() -> None:
    limiter = _build_rate_limiter(
        max_requests=10, window_seconds=60, max_tracked_keys=5, redis_url=None
    )

    assert isinstance(limiter, InMemoryRateLimiter)
    assert limiter._max_tracked_keys == 5


def test_build_rate_limiter_fails_closed_when_redis_package_is_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured redis_url with no installed client must not silently fall back."""
    monkeypatch.setitem(sys.modules, "redis", None)
    monkeypatch.setitem(sys.modules, "redis.asyncio", None)

    with pytest.raises(RuntimeError, match="rate-limit"):
        _build_rate_limiter(
            max_requests=10,
            window_seconds=60,
            max_tracked_keys=100_000,
            redis_url="redis://localhost:6379/0",
        )


def test_build_rate_limiter_returns_redis_backed_limiter_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_redis(monkeypatch)

    limiter = _build_rate_limiter(
        max_requests=10,
        window_seconds=60,
        max_tracked_keys=100_000,
        redis_url="redis://localhost:6379/0",
    )

    assert isinstance(limiter, RedisRateLimiter)


def test_build_rate_limiter_threads_the_fingerprint_secret_into_the_redis_limiter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_redis(monkeypatch)

    limiter = _build_rate_limiter(
        max_requests=10,
        window_seconds=60,
        max_tracked_keys=100_000,
        redis_url="redis://localhost:6379/0",
        fingerprint_secret=b"configured-secret",
    )

    assert isinstance(limiter, RedisRateLimiter)
    assert limiter._fingerprint_secret == b"configured-secret"
