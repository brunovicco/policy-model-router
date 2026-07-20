"""HTTP entrypoint: ``POST /route``.

The model-routing decision endpoint agents call over HTTP before every LLM call. Per ADR-0004,
this service is infrastructure, not an A2A agent: agents call it directly, it is not discovered
through Agent Cards or the A2A protocol.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from policy_model_router.adapters.clock import SystemClock
from policy_model_router.adapters.id_generator import Uuid4IdGenerator
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


def _routing_policy_path() -> Path:
    """Return the configured routing policy file path, defaulting to the shipped config."""
    return Path(os.environ.get("ROUTING_POLICY_PATH", _DEFAULT_ROUTING_POLICY_PATH))


def _service_version() -> str:
    """Return the installed package version, or a placeholder outside a packaged install."""
    try:
        return version(_SERVICE_NAME)
    except PackageNotFoundError:
        return "0.0.0-dev"


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Configure structured logging and load the routing policy once at startup.

    Fails fast if the routing policy is missing or invalid.
    """
    configure_logging(
        service=_SERVICE_NAME,
        environment=os.environ.get("APP_ENV", "development"),
        version=_service_version(),
    )
    policy = load_routing_policy(_routing_policy_path())
    app.state.route_model_use_case = RouteModelUseCase(
        policy, clock=SystemClock(), id_generator=Uuid4IdGenerator()
    )
    yield


app = FastAPI(title="policy-model-router", lifespan=_lifespan)


def get_route_model_use_case(request: Request) -> RouteModelUseCase:
    """Return the use case built at startup from the loaded routing policy."""
    use_case: RouteModelUseCase = request.app.state.route_model_use_case
    return use_case


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


@app.post("/route", response_model=ModelRouteDecision)
async def route(
    request: ModelRouteRequest,
    use_case: Annotated[RouteModelUseCase, Depends(get_route_model_use_case)],
) -> ModelRouteDecision:
    """Evaluate one model-routing request and return the resulting decision record."""
    decision = use_case.route(to_domain_request(request))
    return from_domain_decision(decision)
