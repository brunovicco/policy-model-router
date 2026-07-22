"""Unit tests for ``entrypoints/http.py``'s rate limiter factory (``_build_rate_limiter``).

Injects a fake ``redis``/``redis.asyncio`` module tree via ``sys.modules`` rather than requiring
the real optional ``redis`` package, mirroring ``tests/unit/test_tracing.py``'s pattern for the
Langfuse optional dependency.
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


def test_build_rate_limiter_defaults_to_in_memory_when_redis_url_is_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("REDIS_URL", raising=False)

    limiter = _build_rate_limiter(max_requests=10, window_seconds=60)

    assert isinstance(limiter, InMemoryRateLimiter)


def test_build_rate_limiter_fails_closed_when_redis_package_is_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured REDIS_URL with no installed client must not silently fall back."""
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setitem(sys.modules, "redis", None)
    monkeypatch.setitem(sys.modules, "redis.asyncio", None)

    with pytest.raises(RuntimeError, match="rate-limit"):
        _build_rate_limiter(max_requests=10, window_seconds=60)


def test_build_rate_limiter_returns_redis_backed_limiter_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_redis(monkeypatch)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    limiter = _build_rate_limiter(max_requests=10, window_seconds=60)

    assert isinstance(limiter, RedisRateLimiter)
