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
import hmac
import math
import secrets
from typing import Any

import structlog
from prometheus_client import Counter

logger = structlog.get_logger(__name__)

_KEY_PREFIX = "policy-model-router:rate-limit"
_MAX_WINDOW_SECONDS = 86_400.0

# INCR and setting the TTL happen in one round trip so a crash between the two never leaves a
# counter key without an expiry (which would otherwise block that key forever once it reached the
# limit). Also repairs a key found without a TTL (KEY_PTTL < 0) from before this script existed, or
# from any other cause - self-healing rather than requiring an operator to intervene. Millisecond
# precision (PTTL/PEXPIRE, not TTL/EXPIRE): a sub-second RATE_LIMIT_WINDOW_SECONDS truncated to
# whole seconds would pass EXPIRE a TTL of 0, which Redis treats as "delete immediately" - silently
# disabling enforcement instead of shortening the window.
_INCR_AND_EXPIRE_SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 or redis.call('PTTL', KEYS[1]) < 0 then
    redis.call('PEXPIRE', KEYS[1], ARGV[1])
end
return count
"""

BACKEND_UNAVAILABLE_TOTAL = Counter(
    "policy_model_router_rate_limiter_backend_unavailable_total",
    "Requests where the Redis-backed rate limiter failed open because its backend was "
    "unreachable. A sustained increase means the rate limit is not being enforced.",
)


class RedisRateLimiter:
    """Fixed-window request counter backed by Redis, shared across processes/replicas."""

    def __init__(
        self,
        client: Any,
        *,
        max_requests: int,
        window_seconds: float,
        fingerprint_secret: bytes | None = None,
    ) -> None:
        """Bind an async Redis-compatible client (duck-typed: needs ``eval``/``ping``).

        Args:
            client: An async client exposing ``eval`` and ``ping`` coroutines,
                e.g. ``redis.asyncio.Redis``.
            max_requests: Maximum requests a single key may make within one window.
            window_seconds: Window length, in seconds, in the operational range
                ``(0, 86_400]``.
            fingerprint_secret: HMAC key for the fail-open log fingerprint (see
                :meth:`_fingerprint`). Defaults to a random, process-local secret generated once
                per instance - not stable across restarts, but a caller with only log access can
                never derive it, unlike an unkeyed hash. Pass an explicit secret (e.g. from
                ``RATE_LIMIT_FINGERPRINT_SECRET``) to keep fingerprints stable across restarts for
                longer-lived log correlation.
        """
        if not math.isfinite(window_seconds) or not 0 < window_seconds <= _MAX_WINDOW_SECONDS:
            raise ValueError("window_seconds must be finite and in the range (0, 86400]")

        self._client = client
        self._max_requests = max_requests
        self._window_ms = max(1, round(window_seconds * 1000))
        self._fingerprint_secret = (
            fingerprint_secret if fingerprint_secret is not None else secrets.token_bytes(32)
        )

    def _fingerprint(self, key: str) -> str:
        """Return a short, keyed fingerprint of ``key`` for correlation in logs.

        Never log ``key`` itself: it embeds the caller's IP address
        (``entrypoints/http.py``'s ``rate_limit_key``), which is personal data per
        ``.claude/rules/security-privacy.md``. HMAC-SHA-256 (not a plain hash) so an attacker with
        only log access cannot enumerate the low-entropy ``ip:agent_name`` space and match
        fingerprints without also knowing ``self._fingerprint_secret``.
        """
        return hmac.new(self._fingerprint_secret, key.encode(), hashlib.sha256).hexdigest()[:12]

    async def allow(self, key: str) -> bool:
        """Return whether one more request for ``key`` is within the shared limit.

        Increments the counter and ensures its TTL is set in a single atomic Lua script (see
        ``_INCR_AND_EXPIRE_SCRIPT``), so a crash or error between the increment and the expiry
        can never leave the key without a TTL. Fails open (returns ``True``) on any client error -
        a Redis outage must not block routing traffic - and logs the failure so it is observable
        without becoming a request-path failure.
        """
        full_key = f"{_KEY_PREFIX}:{key}"
        try:
            count = await self._client.eval(_INCR_AND_EXPIRE_SCRIPT, 1, full_key, self._window_ms)
        except Exception:
            BACKEND_UNAVAILABLE_TOTAL.inc()
            logger.warning(
                "rate_limiter_backend_unavailable", key_fingerprint=self._fingerprint(key)
            )
            return True
        return bool(count <= self._max_requests)

    async def ping(self) -> None:
        """Verify connectivity to the backing Redis instance; raises on failure.

        Called only at startup to fail the service closed on misconfiguration; runtime failures
        from :meth:`allow` are handled leniently (fail open) instead.
        """
        await self._client.ping()

    async def close(self) -> None:
        """Release the underlying Redis connection(s); call once during graceful shutdown."""
        await self._client.aclose()
