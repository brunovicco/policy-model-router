"""Truth-table tests for each eliminatory constraint (ADR-0005), in isolation."""

from collections.abc import Callable
from decimal import Decimal

from policy_model_router.domain.catalog import ModelGroupProfile, WorkloadRule
from policy_model_router.domain.constraints import (
    CONSTRAINTS,
    check_agent_allowlist,
    check_availability,
    check_context_window,
    check_data_classification,
    check_max_cost,
    check_max_latency,
    check_risk_level,
    check_structured_output,
    check_tool_calling,
)
from policy_model_router.domain.enums import DataClassification, RiskLevel
from policy_model_router.domain.routing import RouteRequest

MakeRequest = Callable[..., RouteRequest]
MakeProfile = Callable[..., ModelGroupProfile]
MakeRule = Callable[..., WorkloadRule]


def test_constraints_run_in_the_adr_0005_order() -> None:
    """The fixed evaluation order matches ADR-0005's list, exactly and completely."""
    expected = (
        check_data_classification,
        check_risk_level,
        check_structured_output,
        check_tool_calling,
        check_context_window,
        check_max_cost,
        check_max_latency,
        check_availability,
        check_agent_allowlist,
    )
    assert expected == CONSTRAINTS


def test_risk_level_passes_when_group_is_authorized(
    make_request: MakeRequest, make_profile: MakeProfile, make_rule: MakeRule
) -> None:
    request = make_request(risk_level=RiskLevel.CRITICAL)
    profile = make_profile(authorized_risk_levels=frozenset({RiskLevel.CRITICAL}))

    assert check_risk_level(request, profile, make_rule()) is None


def test_risk_level_rejects_when_group_is_not_authorized(
    make_request: MakeRequest, make_profile: MakeProfile, make_rule: MakeRule
) -> None:
    request = make_request(risk_level=RiskLevel.CRITICAL)
    profile = make_profile(authorized_risk_levels=frozenset({RiskLevel.LOW}))

    reason = check_risk_level(request, profile, make_rule())

    assert reason is not None
    assert "critical" in reason


def test_data_classification_passes_when_group_is_authorized(
    make_request: MakeRequest, make_profile: MakeProfile, make_rule: MakeRule
) -> None:
    request = make_request(data_classification=DataClassification.RESTRICTED)
    profile = make_profile(
        authorized_data_classifications=frozenset({DataClassification.RESTRICTED})
    )

    assert check_data_classification(request, profile, make_rule()) is None


def test_data_classification_rejects_when_group_is_not_authorized(
    make_request: MakeRequest, make_profile: MakeProfile, make_rule: MakeRule
) -> None:
    request = make_request(data_classification=DataClassification.RESTRICTED)
    profile = make_profile(authorized_data_classifications=frozenset({DataClassification.PUBLIC}))

    reason = check_data_classification(request, profile, make_rule())

    assert reason is not None
    assert "restricted" in reason


def test_structured_output_passes_when_not_required(
    make_request: MakeRequest, make_profile: MakeProfile, make_rule: MakeRule
) -> None:
    request = make_request(structured_output_required=False)
    profile = make_profile(supports_structured_output=False)

    assert check_structured_output(request, profile, make_rule()) is None


def test_structured_output_passes_when_supported(
    make_request: MakeRequest, make_profile: MakeProfile, make_rule: MakeRule
) -> None:
    request = make_request(structured_output_required=True)
    profile = make_profile(supports_structured_output=True)

    assert check_structured_output(request, profile, make_rule()) is None


def test_structured_output_rejects_when_required_but_unsupported(
    make_request: MakeRequest, make_profile: MakeProfile, make_rule: MakeRule
) -> None:
    request = make_request(structured_output_required=True)
    profile = make_profile(supports_structured_output=False)

    assert check_structured_output(request, profile, make_rule()) is not None


def test_tool_calling_passes_when_not_required(
    make_request: MakeRequest, make_profile: MakeProfile, make_rule: MakeRule
) -> None:
    profile = make_profile(supports_tool_calling=False)
    rule = make_rule(requires_tool_calling=False)

    assert check_tool_calling(make_request(), profile, rule) is None


def test_tool_calling_passes_when_supported(
    make_request: MakeRequest, make_profile: MakeProfile, make_rule: MakeRule
) -> None:
    profile = make_profile(supports_tool_calling=True)
    rule = make_rule(requires_tool_calling=True)

    assert check_tool_calling(make_request(), profile, rule) is None


def test_tool_calling_rejects_when_required_but_unsupported(
    make_request: MakeRequest, make_profile: MakeProfile, make_rule: MakeRule
) -> None:
    profile = make_profile(supports_tool_calling=False)
    rule = make_rule(requires_tool_calling=True)

    assert check_tool_calling(make_request(), profile, rule) is not None


def test_context_window_passes_when_within_limit(
    make_request: MakeRequest, make_profile: MakeProfile, make_rule: MakeRule
) -> None:
    request = make_request(context_tokens_estimated=1_000)
    profile = make_profile(max_context_tokens=1_000)

    assert check_context_window(request, profile, make_rule()) is None


def test_context_window_rejects_when_exceeded(
    make_request: MakeRequest, make_profile: MakeProfile, make_rule: MakeRule
) -> None:
    request = make_request(context_tokens_estimated=1_001)
    profile = make_profile(max_context_tokens=1_000)

    assert check_context_window(request, profile, make_rule()) is not None


def test_max_cost_passes_when_within_ceiling(
    make_request: MakeRequest, make_profile: MakeProfile, make_rule: MakeRule
) -> None:
    request = make_request(
        max_cost_usd=Decimal("0.10"),
        context_tokens_estimated=1_000_000,
        max_output_tokens_estimated=0,
    )
    profile = make_profile(
        input_cost_usd_per_million_tokens=Decimal("0.10"),
        output_cost_usd_per_million_tokens=Decimal("0.00"),
    )

    assert check_max_cost(request, profile, make_rule()) is None


def test_max_cost_rejects_when_exceeding_ceiling(
    make_request: MakeRequest, make_profile: MakeProfile, make_rule: MakeRule
) -> None:
    request = make_request(
        max_cost_usd=Decimal("0.10"),
        context_tokens_estimated=1_100_000,
        max_output_tokens_estimated=0,
    )
    profile = make_profile(
        input_cost_usd_per_million_tokens=Decimal("0.10"),
        output_cost_usd_per_million_tokens=Decimal("0.00"),
    )

    assert check_max_cost(request, profile, make_rule()) is not None


def test_max_latency_passes_when_within_ceiling(
    make_request: MakeRequest, make_profile: MakeProfile, make_rule: MakeRule
) -> None:
    request = make_request(max_latency_ms=5_000)
    profile = make_profile(typical_latency_ms=5_000)

    assert check_max_latency(request, profile, make_rule()) is None


def test_max_latency_rejects_when_exceeding_ceiling(
    make_request: MakeRequest, make_profile: MakeProfile, make_rule: MakeRule
) -> None:
    request = make_request(max_latency_ms=5_000)
    profile = make_profile(typical_latency_ms=5_001)

    assert check_max_latency(request, profile, make_rule()) is not None


def test_availability_passes_when_available(
    make_request: MakeRequest, make_profile: MakeProfile, make_rule: MakeRule
) -> None:
    profile = make_profile(available=True)

    assert check_availability(make_request(), profile, make_rule()) is None


def test_availability_rejects_when_unavailable(
    make_request: MakeRequest, make_profile: MakeProfile, make_rule: MakeRule
) -> None:
    profile = make_profile(available=False)

    assert check_availability(make_request(), profile, make_rule()) is not None


def test_agent_allowlist_passes_when_unrestricted(
    make_request: MakeRequest, make_profile: MakeProfile, make_rule: MakeRule
) -> None:
    profile = make_profile(allowed_agents=frozenset())
    request = make_request(agent_name="any-agent")

    assert check_agent_allowlist(request, profile, make_rule()) is None


def test_agent_allowlist_passes_when_agent_is_listed(
    make_request: MakeRequest, make_profile: MakeProfile, make_rule: MakeRule
) -> None:
    profile = make_profile(allowed_agents=frozenset({"financeiro-agent"}))
    request = make_request(agent_name="financeiro-agent")

    assert check_agent_allowlist(request, profile, make_rule()) is None


def test_agent_allowlist_rejects_when_agent_is_not_listed(
    make_request: MakeRequest, make_profile: MakeProfile, make_rule: MakeRule
) -> None:
    profile = make_profile(allowed_agents=frozenset({"financeiro-agent"}))
    request = make_request(agent_name="cadastral-agent")

    assert check_agent_allowlist(request, profile, make_rule()) is not None
