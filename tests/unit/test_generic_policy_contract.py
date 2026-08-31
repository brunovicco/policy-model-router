"""Contract tests for Phase 1 policy-defined workload/model-group identifiers."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from policy_model_router.adapters.availability import StaticAvailabilityProvider
from policy_model_router.adapters.routing_policy_loader import (
    RoutingPolicyLoadError,
    load_routing_policy,
)
from policy_model_router.application.route_model import (
    IncompleteRoutingPolicyError,
    RouteModelUseCase,
)
from policy_model_router.domain.enums import DataClassification, RiskLevel
from policy_model_router.domain.identifiers import ModelGroupId, WorkloadId
from policy_model_router.domain.routing import RouteRequest
from policy_model_router.entrypoints.contracts import ModelRouteRequest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GENERIC_POLICY = _REPO_ROOT / "examples" / "policies" / "gateway-generic.yaml"
_CREDIT_POLICY = _REPO_ROOT / "examples" / "policies" / "credit-desk-routing.yaml"
_FIXED_NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


class _FixedClock:
    """Deterministic clock for generic-policy contract tests."""

    def now(self) -> datetime:
        """Return the fixed Phase 1 test instant."""
        return _FIXED_NOW


class _FixedIdGenerator:
    """Deterministic ID generator for generic-policy contract tests."""

    def new_id(self) -> str:
        """Return one stable decision identifier."""
        return "decision-phase1"


def _request(workload: str) -> RouteRequest:
    return RouteRequest(
        schema_version="1.0",
        requested_at=_FIXED_NOW,
        workflow_id="phase1-workflow",
        task_id="phase1-task",
        agent_name="gateway",
        workload=WorkloadId(workload),
        risk_level=RiskLevel.MEDIUM,
        data_classification=DataClassification.PUBLIC,
        context_tokens_estimated=1_000,
        max_output_tokens_estimated=500,
        structured_output_required=True,
        max_latency_ms=60_000,
        max_cost_usd=Decimal("1.00"),
    )


@pytest.mark.contract
def test_api_v1_accepts_policy_defined_workload_identifier() -> None:
    request = ModelRouteRequest.model_validate(
        {
            "schema_version": "1.0",
            "requested_at": "2026-08-31T12:00:00Z",
            "workflow_id": "phase1-workflow",
            "task_id": "phase1-task",
            "agent_name": "gateway",
            "workload": "agent.orchestration",
            "risk_level": "medium",
            "data_classification": "public",
            "context_tokens_estimated": 1000,
            "max_output_tokens_estimated": 500,
            "structured_output_required": True,
            "max_latency_ms": 60000,
            "max_cost_usd": "1.00",
        }
    )

    assert request.workload == WorkloadId("agent.orchestration")
    assert request.workload.value == "agent.orchestration"
    assert isinstance(request.workload, WorkloadId)


@pytest.mark.contract
@pytest.mark.parametrize(
    "workload",
    ["Agent.Orchestration", "agent orchestration", ".agent", "agent/execute", "a" * 129],
)
def test_api_v1_rejects_invalid_workload_identifier_syntax(workload: str) -> None:
    with pytest.raises(ValidationError):
        ModelRouteRequest.model_validate(
            {
                "schema_version": "1.0",
                "requested_at": "2026-08-31T12:00:00Z",
                "workflow_id": "phase1-workflow",
                "task_id": "phase1-task",
                "agent_name": "gateway",
                "workload": workload,
                "risk_level": "medium",
                "data_classification": "public",
                "context_tokens_estimated": 1000,
                "max_output_tokens_estimated": 500,
                "structured_output_required": False,
                "max_latency_ms": 60000,
                "max_cost_usd": "1.00",
            }
        )


@pytest.mark.contract
def test_gateway_generic_policy_loads_policy_defined_vocabulary() -> None:
    policy = load_routing_policy(_GENERIC_POLICY)

    assert WorkloadId("agent.orchestration") in policy.workloads
    assert WorkloadId("rag.answer") in policy.workloads
    assert WorkloadId("security.analysis") in policy.workloads
    assert policy.workloads[WorkloadId("agent.orchestration")].model_group == ModelGroupId(
        "agentic-strong"
    )
    assert policy.workloads[WorkloadId("extraction.structured")].model_group == ModelGroupId(
        "structured-fast"
    )


@pytest.mark.contract
def test_credit_desk_policy_remains_reproducible_as_example() -> None:
    policy = load_routing_policy(_CREDIT_POLICY)

    assert policy.policy_id == "credit-desk-routing"
    assert policy.workloads[WorkloadId("document_extraction")].model_group == ModelGroupId(
        "fast-small"
    )
    assert policy.workloads[WorkloadId("cashflow_analysis")].model_group == ModelGroupId(
        "reasoning-medium"
    )
    assert policy.workloads[WorkloadId("json_repair")].model_group == ModelGroupId(
        "fast-structured-output"
    )


@pytest.mark.contract
def test_loader_accepts_arbitrary_policy_defined_identifiers(tmp_path: Path) -> None:
    policy_path = tmp_path / "routing_policy.yaml"
    policy_path.write_text(
        """\
schema_version: "1.0"
policy_id: "custom-policy"
policy_version: "1.0.0"
model_groups:
  custom-balanced:
    authorized_data_classifications: [public]
    authorized_risk_levels: [low]
    supports_structured_output: true
    supports_tool_calling: false
    max_context_tokens: 32000
    typical_latency_ms: 1000
    input_cost_usd_per_million_tokens: "0.10"
    output_cost_usd_per_million_tokens: "0.40"
    available: true
    allowed_agents: []
workloads:
  custom.answer:
    model_group: custom-balanced
    requires_tool_calling: false
""",
        encoding="utf-8",
    )

    policy = load_routing_policy(policy_path)

    assert policy.workloads[WorkloadId("custom.answer")].model_group == ModelGroupId(
        "custom-balanced"
    )


@pytest.mark.contract
def test_loader_fails_closed_on_undefined_model_group_reference(tmp_path: Path) -> None:
    policy_path = tmp_path / "routing_policy.yaml"
    policy_path.write_text(
        """\
schema_version: "1.0"
policy_id: "broken-policy"
policy_version: "1.0.0"
model_groups:
  balanced:
    authorized_data_classifications: [public]
    authorized_risk_levels: [low]
    supports_structured_output: true
    supports_tool_calling: false
    max_context_tokens: 32000
    typical_latency_ms: 1000
    input_cost_usd_per_million_tokens: "0.10"
    output_cost_usd_per_million_tokens: "0.40"
    available: true
    allowed_agents: []
workloads:
  rag.answer:
    model_group: missing-group
    requires_tool_calling: false
""",
        encoding="utf-8",
    )

    with pytest.raises(RoutingPolicyLoadError):
        load_routing_policy(policy_path)


@pytest.mark.anyio
@pytest.mark.contract
async def test_generic_policy_is_deterministic_for_same_request() -> None:
    policy = load_routing_policy(_GENERIC_POLICY)
    use_case = RouteModelUseCase(
        policy,
        clock=_FixedClock(),
        id_generator=_FixedIdGenerator(),
        availability=StaticAvailabilityProvider(),
        service_version="1.0-test",
        environment="test",
    )
    request = _request("agent.orchestration")

    first = await use_case.route(request)
    second = await use_case.route(request)

    assert first == second
    assert first.selected_model_group == ModelGroupId("agentic-strong")


@pytest.mark.anyio
@pytest.mark.contract
async def test_unknown_well_formed_workload_fails_closed() -> None:
    policy = load_routing_policy(_GENERIC_POLICY)
    use_case = RouteModelUseCase(
        policy,
        clock=_FixedClock(),
        id_generator=_FixedIdGenerator(),
        availability=StaticAvailabilityProvider(),
        service_version="1.0-test",
        environment="test",
    )

    with pytest.raises(IncompleteRoutingPolicyError):
        await use_case.route(_request("unknown.workload"))
