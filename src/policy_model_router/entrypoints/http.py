"""HTTP entrypoint: ``POST /route``, plus ``/health``/``/readyz`` and boundary hardening.

The model-routing decision endpoint agents call over HTTP before every LLM call. Per ADR-0004,
this service is infrastructure, not an A2A agent: agents call it directly, it is not discovered
through Agent Cards or the A2A protocol. Per ADR-0007 (amended), ``/route`` requires a per-agent
API key and is rate-limited per (client IP, agent) pair; ``/health`` and ``/readyz`` are
unauthenticated and unthrottled so orchestrators can probe them cheaply.
"""

import json
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from policy_model_router.adapters.availability import StaticAvailabilityProvider
from policy_model_router.adapters.clock import SystemClock
from policy_model_router.adapters.id_generator import Uuid4IdGenerator
from policy_model_router.adapters.rate_limiter import InMemoryRateLimiter, RateLimitExceededError
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


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Configure logging and load the routing policy, API key, and rate limiter at startup.

    Fails fast if the routing policy is missing/invalid or the API key is not configured.
    """
    configure_logging(
        service=_SERVICE_NAME,
        environment=os.environ.get("APP_ENV", "development"),
        version=_service_version(),
    )
    policy = load_routing_policy(_routing_policy_path())
    app.state.route_model_use_case = RouteModelUseCase(
        policy,
        clock=SystemClock(),
        id_generator=Uuid4IdGenerator(),
        availability=StaticAvailabilityProvider(),
    )
    app.state.api_keys = _required_api_keys()
    app.state.rate_limiter = InMemoryRateLimiter(
        max_requests=int(
            os.environ.get("RATE_LIMIT_MAX_REQUESTS", _DEFAULT_RATE_LIMIT_MAX_REQUESTS)
        ),
        window_seconds=float(
            os.environ.get("RATE_LIMIT_WINDOW_SECONDS", _DEFAULT_RATE_LIMIT_WINDOW_SECONDS)
        ),
    )
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


@app.post("/route", response_model=ModelRouteDecision)
async def route(
    request: ModelRouteRequest,
    http_request: Request,
    use_case: Annotated[RouteModelUseCase, Depends(get_route_model_use_case)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> ModelRouteDecision:
    """Evaluate one model-routing request and return the resulting decision record."""
    _authenticate(x_api_key, request.agent_name, http_request.app.state.api_keys)

    client_host = http_request.client.host if http_request.client else "unknown"
    rate_limit_key = f"{client_host}:{request.agent_name}"
    if not http_request.app.state.rate_limiter.allow(rate_limit_key):
        raise RateLimitExceededError(f"rate limit exceeded for {rate_limit_key!r}")

    decision = use_case.route(to_domain_request(request))
    return from_domain_decision(decision)
