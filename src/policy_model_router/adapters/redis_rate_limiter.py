"""Redis-backed rate limiter, shared across service replicas (ADR-0008).

Optional: requires ``REDIS_URL`` and the ``rate-limit`` extra (``uv sync --extra rate-limit``).
This module never imports ``redis`` at module scope - the client is constructed and passed in by
``entrypoints/http.py``'s ``_build_rate_limiter`` factory, which does the deferred import - so this
class stays importable, and its own logic testable against a fake client, without the optional
dependency installed. Never leaks a redis-specific exception type: it fails open on any client
error, because a rate-limiter backend outage must not become a routing outage - but every fail-open
increments a Prometheus counter exposed on ``GET /metrics``, so an extended outage is alertable
rather than only discoverable in logs (ADR-0008's amendment).
"""

import hashlib
from typing import Any

import structlog
from prometheus_client import Counter

logger = structlog.get_logger(__name__)

_KEY_PREFIX = "policy-model-router:rate-limit"

BACKEND_UNAVAILABLE_TOTAL = Counter(
    "policy_model_router_rate_limiter_backend_unavailable_total",
    "Requests where the Redis-backed rate limiter failed open because its backend was "
    "unreachable. A sustained increase means the rate limit is not being enforced.",
)


def _fingerprint(key: str) -> str:
    """Return a short, non-reversible fingerprint of ``key`` for correlation in logs.

    Never log ``key`` itself: it embeds the caller's IP address
    (``entrypoints/http.py``'s ``rate_limit_key``), which is personal data per
    ``.claude/rules/security-privacy.md``. The fingerprint still lets an operator tell whether
    repeated failures come from the same key or many different ones, without recovering the IP.
    """
    return hashlib.sha256(key.encode()).hexdigest()[:12]


class RedisRateLimiter:
    """Fixed-window request counter backed by Redis, shared across processes/replicas."""

    def __init__(self, client: Any, *, max_requests: int, window_seconds: float) -> None:
        """Bind an async Redis-compatible client (duck-typed: needs ``incr``/``expire``/``ping``).

        Args:
            client: An async client exposing ``incr``, ``expire``, and ``ping`` coroutines,
                e.g. ``redis.asyncio.Redis``.
            max_requests: Maximum requests a single key may make within one window.
            window_seconds: Window length, in seconds.
        """
        self._client = client
        self._max_requests = max_requests
        self._window_seconds = window_seconds

    async def allow(self, key: str) -> bool:
        """Return whether one more request for ``key`` is within the shared limit.

        Uses ``INCR`` (atomic) followed by ``EXPIRE`` on the first request of a window; two
        concurrent first requests can each set the TTL, which only ever shortens or matches the
        intended window and is an accepted simplification of a fixed-window counter. Fails open
        (returns ``True``) on any client error - a Redis outage must not block routing traffic -
        and logs the failure so it is observable without becoming a request-path failure.
        """
        full_key = f"{_KEY_PREFIX}:{key}"
        try:
            count = await self._client.incr(full_key)
            if count == 1:
                await self._client.expire(full_key, int(self._window_seconds))
        except Exception:
            BACKEND_UNAVAILABLE_TOTAL.inc()
            logger.warning("rate_limiter_backend_unavailable", key_fingerprint=_fingerprint(key))
            return True
        return bool(count <= self._max_requests)

    async def ping(self) -> None:
        """Verify connectivity to the backing Redis instance; raises on failure.

        Called only at startup to fail the service closed on misconfiguration; runtime failures
        from :meth:`allow` are handled leniently (fail open) instead.
        """
        await self._client.ping()
