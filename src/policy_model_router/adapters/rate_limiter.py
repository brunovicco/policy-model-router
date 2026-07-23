"""In-memory, fixed-window rate limiter for the HTTP entrypoint.

Per ADR-0007, this is a single-process limiter: it has no shared state across replicas or worker
processes. It bounds abuse from a single instance but does not enforce a cluster-wide limit. This
is the default when ``REDIS_URL`` is not configured; see
:class:`~policy_model_router.adapters.redis_rate_limiter.RedisRateLimiter` and ADR-0008 for the
shared alternative.
"""

import time
from collections import OrderedDict
from collections.abc import Callable

_DEFAULT_MAX_TRACKED_KEYS = 100_000


class RateLimitExceededError(Exception):
    """Raised when a key has exceeded its allowed request rate."""


class InMemoryRateLimiter:
    """Fixed-window request counter keyed by an arbitrary string (e.g. ``ip:agent_name``).

    Tracks at most ``max_tracked_keys`` distinct keys, evicting the least-recently-touched one
    once that cap is exceeded, so an unbounded number of distinct keys (e.g. many client IPs over
    a long uptime, or a caller varying the key on purpose) cannot grow this process's memory
    without bound.
    """

    def __init__(
        self,
        *,
        max_requests: int,
        window_seconds: float,
        time_source: Callable[[], float] = time.monotonic,
        max_tracked_keys: int = _DEFAULT_MAX_TRACKED_KEYS,
    ) -> None:
        """Configure the window size, the (injectable) time source, and the tracked-key cap.

        Args:
            max_requests: Maximum requests a single key may make within one window.
            window_seconds: Window length, in seconds.
            time_source: Returns the current monotonic time; overridden in tests for determinism.
            max_tracked_keys: Maximum distinct keys held in memory at once; the
                least-recently-touched key is evicted first when this is exceeded.
        """
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._time_source = time_source
        self._max_tracked_keys = max_tracked_keys
        self._windows: OrderedDict[str, tuple[float, int]] = OrderedDict()

    async def allow(self, key: str) -> bool:
        """Return whether one more request for ``key`` is within its current window.

        Starts a new window for ``key`` if none exists or the previous one has elapsed; otherwise
        increments the count and compares it against the configured limit. Async only to satisfy
        the shared ``entrypoints.http.RateLimiter`` protocol; this implementation performs no I/O
        and never awaits anything.
        """
        now = self._time_source()
        window_start, count = self._windows.get(key, (now, 0))
        if now - window_start >= self._window_seconds:
            window_start, count = now, 0
        count += 1
        self._windows[key] = (window_start, count)
        self._windows.move_to_end(key)
        if len(self._windows) > self._max_tracked_keys:
            self._windows.popitem(last=False)
        return count <= self._max_requests

    async def ping(self) -> None:
        """No-op: an in-process limiter has no backend to verify connectivity to."""
        return

    async def close(self) -> None:
        """No-op: an in-process limiter has no backend connection to release."""
        return
