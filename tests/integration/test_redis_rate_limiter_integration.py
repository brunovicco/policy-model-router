"""Integration tests for :class:`RedisRateLimiter` against a real Redis instance (ADR-0008).

Requires the optional ``redis`` package (``uv sync --extra rate-limit``) and a reachable Redis at
``REDIS_URL`` (default ``redis://localhost:6379/0``; see ``docker compose up -d redis`` in
``docs/DEVELOPMENT.md``). Skips automatically - does not fail - when either is unavailable, so a
plain ``uv run pytest`` still passes without any local infrastructure; CI provides both so this
module actually runs there.
"""

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest

redis_asyncio = pytest.importorskip("redis.asyncio")

from policy_model_router.adapters.redis_rate_limiter import RedisRateLimiter  # noqa: E402

pytestmark = pytest.mark.integration

_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture
async def redis_client() -> AsyncIterator[Any]:
    """A real ``redis.asyncio.Redis`` client, or a skip if none is reachable."""
    client = redis_asyncio.Redis.from_url(
        _REDIS_URL, socket_connect_timeout=1.0, socket_timeout=1.0
    )
    try:
        await client.ping()
    except Exception:
        pytest.skip(f"no reachable Redis at {_REDIS_URL}")
    yield client
    await client.aclose()


@pytest.mark.anyio
async def test_allow_enforces_the_limit_against_real_redis(redis_client: Any) -> None:
    key = f"test:{uuid.uuid4()}"
    limiter = RedisRateLimiter(redis_client, max_requests=2, window_seconds=30)

    assert await limiter.allow(key) is True
    assert await limiter.allow(key) is True
    assert await limiter.allow(key) is False


@pytest.mark.anyio
async def test_allow_is_shared_across_limiter_instances_against_real_redis(
    redis_client: Any,
) -> None:
    """Two limiter instances backed by the same Redis share one counter - the point of ADR-0008."""
    key = f"test:{uuid.uuid4()}"
    limiter_a = RedisRateLimiter(redis_client, max_requests=1, window_seconds=30)
    limiter_b = RedisRateLimiter(redis_client, max_requests=1, window_seconds=30)

    assert await limiter_a.allow(key) is True
    assert await limiter_b.allow(key) is False


@pytest.mark.anyio
async def test_ping_succeeds_against_real_redis(redis_client: Any) -> None:
    limiter = RedisRateLimiter(redis_client, max_requests=1, window_seconds=30)

    await limiter.ping()
