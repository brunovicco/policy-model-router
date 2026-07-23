"""HTTP entrypoint: ``POST /route``, plus ``/health``, ``/readyz``, and ``/metrics``.

The model-routing decision endpoint agents call over HTTP before every LLM call. Per ADR-0004,
this service is infrastructure, not an A2A agent: agents call it directly, it is not discovered
through Agent Cards or the A2A protocol. Per ADR-0007 (amended) and ADR-0008's second amendment,
``/route`` requires a per-agent API key and is rate-limited on two tiers - a light per-client-IP
tier, then a per-(IP, agent) tier - before authentication, so repeated invalid-API-key attempts
are throttled too, and an attacker cannot bypass the per-agent tier merely by varying the claimed
``agent_name``. ``/health``, ``/readyz``, and ``/metrics`` are unauthenticated and unthrottled so
orchestrators and scrapers can probe them cheaply.
"""

import json
import os
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated, Protocol

from fastapi import Depends, FastAPI, Header, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from policy_model_router.adapters.availability import StaticAvailabilityProvider
from policy_model_router.adapters.clock import SystemClock
from policy_model_router.adapters.id_generator import Uuid4IdGenerator
from policy_model_router.adapters.rate_limiter import InMemoryRateLimiter, RateLimitExceededError
from policy_model_router.adapters.redis_rate_limiter import RedisRateLimiter
from policy_model_router.adapters.routing_policy_loader import load_routing_policy
from policy_model_router.application.route_model import (
    IncompleteRoutingPolicyError,
    RouteModelUseCase,
)
from policy_model_router.domain.routing import NoViableModelGroupError
from policy_model_router.entrypoints.contracts import (
    ModelRouteDecision,
    ModelRouteRequest,
    from_domain_decision,
    to_domain_request,
)
from policy_model_router.entrypoints.logging import configure_logging

_SERVICE_NAME = "policy-model-router"
_DEFAULT_ROUTING_POLICY_PATH = "config/routing_policy.yaml"
_DEFAULT_RATE_LIMIT_MAX_REQUESTS = 60
_DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 60.0
_DEFAULT_RATE_LIMIT_MAX_TRACKED_KEYS = 100_000
_DEFAULT_RATE_LIMIT_PER_IP_MAX_REQUESTS = 600

ROUTE_DECISIONS_TOTAL = Counter(
    "policy_model_router_route_decisions_total",
    "Successful routing decisions, labeled by workload and the selected model group.",
    ["workload", "model_group"],
)
ROUTE_REJECTIONS_TOTAL = Counter(
    "policy_model_router_route_rejections_total",
    "Routing requests that did not produce a decision, labeled by workload and outcome.",
    ["workload", "outcome"],
)
ROUTE_DURATION_SECONDS = Histogram(
    "policy_model_router_route_duration_seconds",
    "Time spent evaluating one routing decision (RouteModelUseCase.route only), by workload.",
    ["workload"],
)
RATE_LIMIT_DECISIONS_TOTAL = Counter(
    "policy_model_router_rate_limit_decisions_total",
    "Rate limiter admit/block decisions, labeled by tier (per_ip, per_agent) and outcome.",
    ["tier", "outcome"],
)


class RateLimiter(Protocol):
    """Port for admitting or rejecting one more request for a given key.

    Defined here, not in ``application/ports.py``: this protects the HTTP boundary only - the
    application use case never rate-limits anything - so the consumer-side port lives next to its
    only consumer, this module. Two implementations exist: ``InMemoryRateLimiter`` (default,
    per-process) and the optional, Redis-backed ``RedisRateLimiter`` shared across replicas
    (ADR-0008).
    """

    async def allow(self, key: str) -> bool:
        """Return whether one more request for ``key`` is within its current limit."""
        ...

    async def ping(self) -> None:
        """Verify the limiter's backend is reachable; raise if it is not.

        Called once at startup so a misconfigured backend fails the service closed immediately,
        rather than surfacing as a per-request failure later.
        """
        ...


class AuthenticationError(Exception):
    """Raised when a request to a protected route has a missing or invalid API key."""


def _routing_policy_path() -> Path:
    """Return the configured routing policy file path, defaulting to the shipped config."""
    return Path(os.environ.get("ROUTING_POLICY_PATH", _DEFAULT_ROUTING_POLICY_PATH))


def _service_version() -> str:
    """Return the installed package version, or a placeholder outside a packaged install."""
    try:
        return version(_SERVICE_NAME)
    except PackageNotFoundError:
        return "0.0.0-dev"


def _required_api_keys() -> dict[str, str]:
    """Return the configured per-agent API keys, failing fast if they are missing or malformed.

    Deny-by-default: an unset, empty, or malformed ``API_KEYS`` stops the service from starting
    rather than serving ``/route`` unauthenticated. Per ADR-0007's amendment, this is a mapping of
    ``agent_name`` to that agent's own key, not one shared secret for every caller.
    """
    raw = os.environ.get("API_KEYS", "")
    if not raw:
        raise RuntimeError("API_KEYS environment variable is required to start this service")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            "API_KEYS environment variable must be a JSON object mapping agent_name to API key"
        ) from exc
    if (
        not isinstance(parsed, dict)
        or not parsed
        or not all(isinstance(k, str) and isinstance(v, str) and v for k, v in parsed.items())
    ):
        raise RuntimeError(
            "API_KEYS environment variable must be a non-empty JSON object of "
            "non-empty string agent_name/key pairs"
        )
    return parsed


def _fingerprint_secret() -> bytes | None:
    """Return the configured rate-limiter fingerprint HMAC secret, or ``None`` for an ephemeral one.

    Unset by default: ``RedisRateLimiter`` then falls back to a random, process-local secret,
    which still defeats offline enumeration of the low-entropy ``(IP, agent_name)`` key space from
    log access alone, but does not stay stable across restarts. Set the env var to keep
    fingerprints stable across restarts for longer-lived log correlation; treat it as a secret.
    """
    raw = os.environ.get("RATE_LIMIT_FINGERPRINT_SECRET")
    return raw.encode() if raw else None


def _build_rate_limiter(
    *, max_requests: int, window_seconds: float, fingerprint_secret: bytes | None = None
) -> RateLimiter:
    """Build the configured rate limiter.

    Redis-backed and shared across replicas if ``REDIS_URL`` is set (ADR-0008); otherwise the
    default in-memory, per-process limiter. Raises ``RuntimeError`` (fail closed) if ``REDIS_URL``
    is set but the optional ``redis`` package is not installed - a silent fallback to per-process
    behavior would defeat the reason the operator configured it.
    """
    redis_url = os.environ.get("REDIS_URL", "")
    if not redis_url:
        max_tracked_keys = int(
            os.environ.get("RATE_LIMIT_MAX_TRACKED_KEYS", _DEFAULT_RATE_LIMIT_MAX_TRACKED_KEYS)
        )
        return InMemoryRateLimiter(
            max_requests=max_requests,
            window_seconds=window_seconds,
            max_tracked_keys=max_tracked_keys,
        )

    try:
        from redis.asyncio import Redis
    except ImportError as exc:
        raise RuntimeError(
            "REDIS_URL is set but the 'redis' package is not installed; "
            "install it with `uv sync --extra rate-limit`"
        ) from exc

    client = Redis.from_url(redis_url, socket_connect_timeout=2.0, socket_timeout=2.0)
    return RedisRateLimiter(
        client,
        max_requests=max_requests,
        window_seconds=window_seconds,
        fingerprint_secret=fingerprint_secret,
    )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Configure logging and load the routing policy, API keys, and rate limiters at startup.

    Fails fast if the routing policy is missing/invalid, the API keys are not configured, or
    either rate limiter's backend (when Redis-backed) is not reachable.
    """
    environment = os.environ.get("APP_ENV", "development")
    service_version = _service_version()
    configure_logging(service=_SERVICE_NAME, environment=environment, version=service_version)

    policy = load_routing_policy(_routing_policy_path())
    app.state.route_model_use_case = RouteModelUseCase(
        policy,
        clock=SystemClock(),
        id_generator=Uuid4IdGenerator(),
        availability=StaticAvailabilityProvider(),
        service_version=service_version,
        environment=environment,
    )
    app.state.api_keys = _required_api_keys()

    fingerprint_secret = _fingerprint_secret()
    window_seconds = float(
        os.environ.get("RATE_LIMIT_WINDOW_SECONDS", _DEFAULT_RATE_LIMIT_WINDOW_SECONDS)
    )
    rate_limiter = _build_rate_limiter(
        max_requests=int(
            os.environ.get("RATE_LIMIT_MAX_REQUESTS", _DEFAULT_RATE_LIMIT_MAX_REQUESTS)
        ),
        window_seconds=window_seconds,
        fingerprint_secret=fingerprint_secret,
    )
    ip_rate_limiter = _build_rate_limiter(
        max_requests=int(
            os.environ.get(
                "RATE_LIMIT_PER_IP_MAX_REQUESTS", _DEFAULT_RATE_LIMIT_PER_IP_MAX_REQUESTS
            )
        ),
        window_seconds=window_seconds,
        fingerprint_secret=fingerprint_secret,
    )
    try:
        await rate_limiter.ping()
        await ip_rate_limiter.ping()
    except Exception as exc:
        raise RuntimeError(f"rate limiter backend is not reachable: {exc}") from exc
    app.state.rate_limiter = rate_limiter
    app.state.ip_rate_limiter = ip_rate_limiter

    yield


app = FastAPI(title="policy-model-router", lifespan=_lifespan)


def get_route_model_use_case(request: Request) -> RouteModelUseCase:
    """Return the use case built at startup from the loaded routing policy."""
    use_case: RouteModelUseCase = request.app.state.route_model_use_case
    return use_case


def _authenticate(x_api_key: str | None, agent_name: str, api_keys: dict[str, str]) -> None:
    """Reject unless ``x_api_key`` matches the configured key for ``agent_name``.

    Looks the key up by claimed agent identity rather than accepting any configured key for any
    agent, so one agent's key can be rotated or revoked without affecting the others (ADR-0007's
    amendment). The error is identical whether the agent is unknown or the key is wrong, so the
    response never reveals which agent names are configured.
    """
    configured_key = api_keys.get(agent_name)
    if (
        configured_key is None
        or x_api_key is None
        or not secrets.compare_digest(x_api_key, configured_key)
    ):
        raise AuthenticationError(f"missing or invalid API key for agent {agent_name!r}")


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    """Build the stable, machine-readable error envelope used by every handler below."""
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message}}
    )


@app.exception_handler(RequestValidationError)
async def _handle_validation_error(_request: Request, _exc: RequestValidationError) -> JSONResponse:
    """Map a malformed request body to a stable 422 envelope, without leaking schema internals."""
    return _error_response(
        422, "invalid_request", "the request body does not match the expected schema"
    )


@app.exception_handler(NoViableModelGroupError)
async def _handle_no_viable_model_group(
    _request: Request, exc: NoViableModelGroupError
) -> JSONResponse:
    """Map a hard routing failure (target group ineligible, no MVP fallback) to a 422 envelope."""
    return _error_response(422, "no_viable_model_group", str(exc))


@app.exception_handler(IncompleteRoutingPolicyError)
async def _handle_incomplete_policy(
    _request: Request, _exc: IncompleteRoutingPolicyError
) -> JSONResponse:
    """Map a routing-policy configuration defect to a 500 envelope, without leaking internals."""
    return _error_response(
        500, "misconfigured_routing_policy", "the routing policy is misconfigured"
    )


@app.exception_handler(AuthenticationError)
async def _handle_authentication_error(
    _request: Request, _exc: AuthenticationError
) -> JSONResponse:
    """Map a missing or invalid API key to a stable 401 envelope."""
    return _error_response(401, "unauthorized", "missing or invalid API key")


@app.exception_handler(RateLimitExceededError)
async def _handle_rate_limit_exceeded(
    _request: Request, _exc: RateLimitExceededError
) -> JSONResponse:
    """Map an exceeded rate limit to a stable 429 envelope."""
    return _error_response(429, "rate_limit_exceeded", "too many requests, try again later")


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe: always returns 200 once the process is serving requests."""
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(request: Request) -> dict[str, str]:
    """Readiness probe: 200 once the routing policy loaded successfully at startup.

    This is a shallow check: the service has no external dependency to probe (per ADR-0004), so
    readiness here means "startup completed", not "a downstream system is healthy".
    """
    _ = request.app.state.route_model_use_case
    return {"status": "ready"}


@app.get("/metrics")
async def metrics() -> Response:
    """Expose Prometheus-format metrics, including the Redis rate limiter's failure counter.

    Includes ``policy_model_router_rate_limiter_backend_unavailable_total`` (ADR-0008's
    amendment): alert on a sustained increase, which means the Redis-backed rate limiter is
    failing open and the configured limit is not being enforced.
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/route", response_model=ModelRouteDecision)
async def route(
    request: ModelRouteRequest,
    http_request: Request,
    use_case: Annotated[RouteModelUseCase, Depends(get_route_model_use_case)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> ModelRouteDecision:
    """Evaluate one model-routing request and return the resulting decision record."""
    client_host = http_request.client.host if http_request.client else "unknown"

    ip_allowed = await http_request.app.state.ip_rate_limiter.allow(f"ip:{client_host}")
    RATE_LIMIT_DECISIONS_TOTAL.labels(
        tier="per_ip", outcome="allowed" if ip_allowed else "blocked"
    ).inc()
    if not ip_allowed:
        raise RateLimitExceededError(f"rate limit exceeded for client {client_host!r}")

    rate_limit_key = f"{client_host}:{request.agent_name}"
    agent_allowed = await http_request.app.state.rate_limiter.allow(rate_limit_key)
    RATE_LIMIT_DECISIONS_TOTAL.labels(
        tier="per_agent", outcome="allowed" if agent_allowed else "blocked"
    ).inc()
    if not agent_allowed:
        raise RateLimitExceededError(f"rate limit exceeded for {rate_limit_key!r}")

    _authenticate(x_api_key, request.agent_name, http_request.app.state.api_keys)

    workload = request.workload.value
    started_at = time.monotonic()
    try:
        decision = await use_case.route(to_domain_request(request))
    except NoViableModelGroupError:
        ROUTE_REJECTIONS_TOTAL.labels(workload=workload, outcome="no_viable_model_group").inc()
        raise
    except IncompleteRoutingPolicyError:
        ROUTE_REJECTIONS_TOTAL.labels(workload=workload, outcome="misconfigured_policy").inc()
        raise
    finally:
        ROUTE_DURATION_SECONDS.labels(workload=workload).observe(time.monotonic() - started_at)

    ROUTE_DECISIONS_TOTAL.labels(
        workload=workload, model_group=decision.selected_model_group.value
    ).inc()
    return from_domain_decision(decision)
