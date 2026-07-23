"""Shared factory fixtures for routing domain objects.

Defaults are maximally permissive (a request/profile/rule that passes every constraint) so each
test can override only the field relevant to the constraint under test.
"""

from collections.abc import Callable, Generator
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
import structlog

from policy_model_router.adapters.redis_rate_limiter import logger as _redis_rate_limiter_logger
from policy_model_router.domain.catalog import ModelGroupProfile, WorkloadRule
from policy_model_router.domain.enums import DataClassification, ModelGroup, RiskLevel, Workload
from policy_model_router.domain.routing import RouteRequest
from policy_model_router.entrypoints.http import logger as _http_logger


@pytest.fixture
def anyio_backend() -> str:
    """Run ``@pytest.mark.anyio`` async tests on asyncio only (anyio is already a dependency of
    fastapi/httpx; this avoids adding pytest-asyncio just to run a handful of async tests).
    """
    return "asyncio"


@pytest.fixture(autouse=True)
def _reset_structlog() -> Generator[None]:
    """Reset structlog's global configuration and logger cache after every test.

    Several tests call ``configure_logging()`` (via ``TestClient(app)``'s lifespan) or
    ``structlog.testing.capture_logs()``, both of which mutate structlog's *process-global*
    configuration. ``structlog.reset_defaults()`` undoes that, but it cannot undo
    ``cache_logger_on_first_use``'s effect on an already-resolved module-level logger: structlog
    caches a resolved logger by monkeypatching ``.bind`` directly onto that specific proxy
    *instance* (see ``structlog._config.BoundLoggerLazyProxy.bind``), which no amount of
    reconfiguring the global config can reverse. ``redis_rate_limiter.py``'s and ``http.py``'s
    module-level ``logger``s are exactly such proxies: the first test that logs through either
    while ``cache_logger_on_first_use=True`` (set by ``configure_logging()``) permanently pins it
    to that run's processor chain, silently breaking every later test's ``capture_logs()``
    assertions regardless of ordering. Popping the instance-level ``bind`` override restores each
    proxy to its lazy, unresolved state so the next test starts clean.
    """
    yield
    structlog.contextvars.clear_contextvars()
    structlog.reset_defaults()
    vars(_redis_rate_limiter_logger).pop("bind", None)
    vars(_http_logger).pop("bind", None)


@pytest.fixture
def make_request() -> Callable[..., RouteRequest]:
    """Return a factory building a permissive ``RouteRequest``, field overrides accepted."""

    def _make(**overrides: Any) -> RouteRequest:
        fields: dict[str, Any] = {
            "schema_version": "1.0",
            "requested_at": datetime(2026, 1, 1, tzinfo=UTC),
            "workflow_id": "workflow-1",
            "task_id": "task-1",
            "agent_name": "test-agent",
            "workload": Workload.CASHFLOW_ANALYSIS,
            "risk_level": RiskLevel.MEDIUM,
            "data_classification": DataClassification.INTERNAL,
            "context_tokens_estimated": 1_000,
            "max_output_tokens_estimated": 500,
            "structured_output_required": False,
            "max_latency_ms": 10_000,
            "max_cost_usd": Decimal("1.00"),
        }
        fields.update(overrides)
        return RouteRequest(**fields)

    return _make


@pytest.fixture
def make_profile() -> Callable[..., ModelGroupProfile]:
    """Return a factory building a permissive ``ModelGroupProfile``, field overrides accepted."""

    def _make(**overrides: Any) -> ModelGroupProfile:
        fields: dict[str, Any] = {
            "authorized_data_classifications": frozenset(DataClassification),
            "authorized_risk_levels": frozenset(RiskLevel),
            "supports_structured_output": True,
            "supports_tool_calling": True,
            "max_context_tokens": 100_000,
            "typical_latency_ms": 1_000,
            "input_cost_usd_per_million_tokens": Decimal("0.10"),
            "output_cost_usd_per_million_tokens": Decimal("0.40"),
            "available": True,
            "allowed_agents": frozenset(),
        }
        fields.update(overrides)
        return ModelGroupProfile(**fields)

    return _make


@pytest.fixture
def make_rule() -> Callable[..., WorkloadRule]:
    """Return a factory building a permissive ``WorkloadRule``, field overrides accepted."""

    def _make(**overrides: Any) -> WorkloadRule:
        fields: dict[str, Any] = {
            "model_group": ModelGroup.REASONING_MEDIUM,
            "requires_tool_calling": False,
        }
        fields.update(overrides)
        return WorkloadRule(**fields)

    return _make
