"""Unit tests for the ADR-0005 model-routing use case.

Uses only synthetic, in-memory ``RoutingPolicy`` fixtures - no filesystem access - per
``.claude/rules/testing.md``. The shipped ``config/routing_policy.yaml`` file itself is exercised
by ``tests/unit/test_routing_policy_loader.py``.
"""

import types
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from policy_model_router.adapters.availability import StaticAvailabilityProvider
from policy_model_router.application.route_model import (
    IncompleteRoutingPolicyError,
    RouteModelUseCase,
)
from policy_model_router.domain.catalog import ModelGroupProfile, RoutingPolicy, WorkloadRule
from policy_model_router.domain.enums import (
    DataClassification,
    ModelGroup,
    ReasonCode,
    RiskLevel,
    Workload,
)
from policy_model_router.domain.routing import NoViableModelGroupError, RouteRequest

MakeRequest = Callable[..., RouteRequest]
MakeProfile = Callable[..., ModelGroupProfile]
MakeRule = Callable[..., WorkloadRule]
_FIXED_NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)
_TEST_POLICY_ID = "test-policy"
_TEST_POLICY_VERSION = "1.0.0"
_TEST_POLICY_DIGEST = "sha256:test"
_TEST_SERVICE_VERSION = "0.0.0-test"
_TEST_ENVIRONMENT = "test"


class _FixedClock:
    """Deterministic clock stub returning a single fixed instant."""

    def now(self) -> datetime:
        """Return the fixed instant configured for this stub."""
        return _FIXED_NOW


class _FixedIdGenerator:
    """Deterministic id generator stub returning a single fixed id."""

    def new_id(self) -> str:
        """Return the fixed id configured for this stub."""
        return "decision-1"


@pytest.fixture
def reference_policy(make_profile: MakeProfile, make_rule: MakeRule) -> RoutingPolicy:
    """Build an in-memory policy shaped like the shipped ``config/routing_policy.yaml``."""
    external_only = frozenset({DataClassification.PUBLIC, DataClassification.INTERNAL})
    local_backed = frozenset(DataClassification)
    up_to_medium_risk = frozenset({RiskLevel.LOW, RiskLevel.MEDIUM})
    up_to_high_risk = frozenset({RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH})
    every_risk_level = frozenset(RiskLevel)
    model_groups = types.MappingProxyType(
        {
            ModelGroup.FAST_SMALL: make_profile(
                authorized_data_classifications=external_only,
                authorized_risk_levels=up_to_medium_risk,
                supports_structured_output=False,
                max_context_tokens=16_000,
                typical_latency_ms=3_000,
                input_cost_usd_per_million_tokens=Decimal("0.10"),
                output_cost_usd_per_million_tokens=Decimal("0.40"),
            ),
            ModelGroup.REASONING_MEDIUM: make_profile(
                authorized_data_classifications=local_backed,
                authorized_risk_levels=up_to_high_risk,
                supports_structured_output=False,
                max_context_tokens=64_000,
                typical_latency_ms=15_000,
                input_cost_usd_per_million_tokens=Decimal("0.50"),
                output_cost_usd_per_million_tokens=Decimal("1.50"),
            ),
            ModelGroup.REASONING_STRONG: make_profile(
                authorized_data_classifications=local_backed,
                authorized_risk_levels=every_risk_level,
                supports_structured_output=False,
                max_context_tokens=128_000,
                typical_latency_ms=30_000,
                input_cost_usd_per_million_tokens=Decimal("2.00"),
                output_cost_usd_per_million_tokens=Decimal("8.00"),
            ),
            ModelGroup.FAST_STRUCTURED_OUTPUT: make_profile(
                authorized_data_classifications=external_only,
                authorized_risk_levels=up_to_medium_risk,
                supports_structured_output=True,
                supports_tool_calling=False,
                max_context_tokens=8_000,
                typical_latency_ms=2_000,
                input_cost_usd_per_million_tokens=Decimal("0.10"),
                output_cost_usd_per_million_tokens=Decimal("0.40"),
            ),
        }
    )
    workloads = types.MappingProxyType(
        {
            Workload.DOCUMENT_EXTRACTION: make_rule(model_group=ModelGroup.FAST_SMALL),
            Workload.CASHFLOW_ANALYSIS: make_rule(model_group=ModelGroup.REASONING_MEDIUM),
            Workload.FINDINGS_CORRELATION: make_rule(model_group=ModelGroup.REASONING_STRONG),
            Workload.OPINION_DRAFTING: make_rule(model_group=ModelGroup.REASONING_STRONG),
            Workload.JSON_REPAIR: make_rule(model_group=ModelGroup.FAST_STRUCTURED_OUTPUT),
        }
    )
    return RoutingPolicy(
        schema_version="1.0",
        policy_id=_TEST_POLICY_ID,
        policy_version=_TEST_POLICY_VERSION,
        policy_digest=_TEST_POLICY_DIGEST,
        model_groups=model_groups,
        workloads=workloads,
    )


@pytest.fixture
def use_case(reference_policy: RoutingPolicy) -> RouteModelUseCase:
    """Build the use case under test, wired to deterministic clock/id-generator stubs."""
    return RouteModelUseCase(
        reference_policy,
        clock=_FixedClock(),
        id_generator=_FixedIdGenerator(),
        availability=StaticAvailabilityProvider(),
        service_version=_TEST_SERVICE_VERSION,
        environment=_TEST_ENVIRONMENT,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("workload", "expected_group"),
    [
        (Workload.DOCUMENT_EXTRACTION, ModelGroup.FAST_SMALL),
        (Workload.CASHFLOW_ANALYSIS, ModelGroup.REASONING_MEDIUM),
        (Workload.FINDINGS_CORRELATION, ModelGroup.REASONING_STRONG),
        (Workload.OPINION_DRAFTING, ModelGroup.REASONING_STRONG),
        (Workload.JSON_REPAIR, ModelGroup.FAST_STRUCTURED_OUTPUT),
    ],
)
async def test_route_selects_the_workload_mapped_group_when_it_is_viable(
    workload: Workload,
    expected_group: ModelGroup,
    use_case: RouteModelUseCase,
    make_request: MakeRequest,
) -> None:
    request = make_request(
        workload=workload,
        data_classification=DataClassification.PUBLIC,
        max_cost_usd=Decimal("1.00"),
        max_latency_ms=60_000,
        context_tokens_estimated=100,
    )

    decision = await use_case.route(request)

    assert decision.selected_model_group == expected_group
    rejected_groups = {candidate.model_group for candidate in decision.rejected_candidates}
    assert rejected_groups == set(ModelGroup) - {expected_group}
    assert all(candidate.reason for candidate in decision.rejected_candidates)
    assert decision.policy_id == _TEST_POLICY_ID
    assert decision.policy_version == _TEST_POLICY_VERSION
    assert decision.policy_digest == _TEST_POLICY_DIGEST
    assert decision.service_version == _TEST_SERVICE_VERSION
    assert decision.environment == _TEST_ENVIRONMENT


@pytest.mark.anyio
async def test_route_rejects_a_non_target_group_that_fails_a_constraint(
    use_case: RouteModelUseCase, make_request: MakeRequest
) -> None:
    request = make_request(
        workload=Workload.CASHFLOW_ANALYSIS,
        data_classification=DataClassification.RESTRICTED,
        max_latency_ms=60_000,
    )

    decision = await use_case.route(request)

    assert decision.selected_model_group == ModelGroup.REASONING_MEDIUM
    reasons = {c.model_group: c.reason for c in decision.rejected_candidates}
    assert "restricted" in reasons[ModelGroup.FAST_SMALL]
    assert "restricted" in reasons[ModelGroup.FAST_STRUCTURED_OUTPUT]
    codes = {c.model_group: c.reason_code for c in decision.rejected_candidates}
    assert codes[ModelGroup.FAST_SMALL] == ReasonCode.DATA_CLASSIFICATION_NOT_AUTHORIZED
    assert codes[ModelGroup.FAST_STRUCTURED_OUTPUT] == ReasonCode.DATA_CLASSIFICATION_NOT_AUTHORIZED
    assert codes[ModelGroup.REASONING_STRONG] == ReasonCode.WORKLOAD_MAPPED_ELSEWHERE
    mapped_elsewhere = next(
        c for c in decision.rejected_candidates if c.model_group == ModelGroup.REASONING_STRONG
    )
    assert mapped_elsewhere.observed_value == Workload.CASHFLOW_ANALYSIS.value
    assert mapped_elsewhere.required_value == ModelGroup.REASONING_MEDIUM.value


@pytest.mark.anyio
async def test_route_rejects_multiple_groups_via_different_constraints_simultaneously(
    use_case: RouteModelUseCase, make_request: MakeRequest
) -> None:
    request = make_request(
        workload=Workload.FINDINGS_CORRELATION,
        data_classification=DataClassification.RESTRICTED,
        context_tokens_estimated=100_000,
        max_latency_ms=60_000,
    )

    decision = await use_case.route(request)

    assert decision.selected_model_group == ModelGroup.REASONING_STRONG
    reasons = {c.model_group: c.reason for c in decision.rejected_candidates}
    assert "restricted" in reasons[ModelGroup.FAST_SMALL]
    assert "exceeds group limit" in reasons[ModelGroup.REASONING_MEDIUM]


@pytest.mark.anyio
async def test_route_raises_when_the_workload_mapped_group_itself_is_eliminated(
    use_case: RouteModelUseCase, make_request: MakeRequest
) -> None:
    request = make_request(
        workload=Workload.DOCUMENT_EXTRACTION,
        data_classification=DataClassification.RESTRICTED,
    )

    with pytest.raises(NoViableModelGroupError) as excinfo:
        await use_case.route(request)

    decision = excinfo.value.decision
    assert decision.rejected_model_group == ModelGroup.FAST_SMALL
    assert decision.workload == Workload.DOCUMENT_EXTRACTION
    assert decision.routing_decision_id == "decision-1"
    assert decision.decided_at == _FIXED_NOW
    assert decision.workflow_id == request.workflow_id
    assert decision.task_id == request.task_id
    assert decision.reason_code == ReasonCode.DATA_CLASSIFICATION_NOT_AUTHORIZED
    assert decision.policy_id == _TEST_POLICY_ID
    assert decision.policy_version == _TEST_POLICY_VERSION
    assert decision.policy_digest == _TEST_POLICY_DIGEST
    assert decision.service_version == _TEST_SERVICE_VERSION
    assert decision.environment == _TEST_ENVIRONMENT


@pytest.mark.anyio
async def test_route_raises_incomplete_policy_error_when_workload_has_no_mapping(
    make_request: MakeRequest,
) -> None:
    empty_policy = RoutingPolicy(
        schema_version="1.0",
        policy_id=_TEST_POLICY_ID,
        policy_version=_TEST_POLICY_VERSION,
        policy_digest=_TEST_POLICY_DIGEST,
        model_groups=types.MappingProxyType({}),
        workloads=types.MappingProxyType({}),
    )
    empty_use_case = RouteModelUseCase(
        empty_policy,
        clock=_FixedClock(),
        id_generator=_FixedIdGenerator(),
        availability=StaticAvailabilityProvider(),
        service_version=_TEST_SERVICE_VERSION,
        environment=_TEST_ENVIRONMENT,
    )

    with pytest.raises(IncompleteRoutingPolicyError):
        await empty_use_case.route(make_request(workload=Workload.CASHFLOW_ANALYSIS))


@pytest.mark.anyio
async def test_route_is_deterministic_for_the_same_request(
    use_case: RouteModelUseCase, make_request: MakeRequest
) -> None:
    request = make_request(
        workload=Workload.CASHFLOW_ANALYSIS, risk_level=RiskLevel.HIGH, max_latency_ms=60_000
    )

    first = await use_case.route(request)
    second = await use_case.route(request)

    assert first == second


@pytest.mark.anyio
async def test_route_rejects_the_mapped_group_when_risk_level_is_not_authorized(
    use_case: RouteModelUseCase, make_request: MakeRequest
) -> None:
    request = make_request(
        workload=Workload.CASHFLOW_ANALYSIS,
        risk_level=RiskLevel.CRITICAL,
        max_latency_ms=60_000,
    )

    with pytest.raises(NoViableModelGroupError) as excinfo:
        await use_case.route(request)

    assert excinfo.value.decision.rejected_model_group == ModelGroup.REASONING_MEDIUM
    assert "critical" in excinfo.value.decision.reason


class _AlwaysUnavailable:
    """Availability provider stub that overrides every group to unavailable."""

    async def is_available(self, _model_group: ModelGroup, _declared_available: bool) -> bool:
        """Always report unavailable, regardless of the policy's declared flag."""
        return False


@pytest.mark.anyio
async def test_route_rejects_a_group_the_availability_provider_marks_unavailable(
    reference_policy: RoutingPolicy, make_request: MakeRequest
) -> None:
    use_case = RouteModelUseCase(
        reference_policy,
        clock=_FixedClock(),
        id_generator=_FixedIdGenerator(),
        availability=_AlwaysUnavailable(),
        service_version=_TEST_SERVICE_VERSION,
        environment=_TEST_ENVIRONMENT,
    )
    request = make_request(workload=Workload.CASHFLOW_ANALYSIS, max_latency_ms=60_000)

    with pytest.raises(NoViableModelGroupError) as excinfo:
        await use_case.route(request)

    assert excinfo.value.decision.rejected_model_group == ModelGroup.REASONING_MEDIUM
    assert "unavailable" in excinfo.value.decision.reason
