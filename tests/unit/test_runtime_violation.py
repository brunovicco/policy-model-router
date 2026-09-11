"""P1.4 tests for content-minimized runtime violation evidence."""

from datetime import UTC, datetime
from decimal import Decimal

from policy_model_router.domain.enums import (
    DataClassification,
    RiskLevel,
    Workload,
)
from policy_model_router.domain.routing import RouteRequest
from policy_model_router.entrypoints.runtime_violation import (
    build_runtime_violation,
    violation_category,
)
from policy_model_router.entrypoints.runtime_violation_contract import (
    RuntimeViolationAuthorizationState,
    RuntimeViolationCategory,
    RuntimeViolationEnvelope,
)


def _request() -> RouteRequest:
    return RouteRequest(
        schema_version="1.0",
        requested_at=datetime(2026, 8, 7, 20, 0, tzinfo=UTC),
        workflow_id="credit-analysis-2026-001",
        task_id="draft-opinion",
        agent_name="Agente de Parecer de Crédito PJ",
        workload=Workload.OPINION_DRAFTING,
        risk_level=RiskLevel.HIGH,
        data_classification=DataClassification.RESTRICTED,
        context_tokens_estimated=3000,
        max_output_tokens_estimated=900,
        structured_output_required=False,
        max_latency_ms=30000,
        max_cost_usd=Decimal("0.30"),
    )


def test_absent_authorization_produces_minimized_digest_bound_event() -> None:
    envelope = build_runtime_violation(
        code="runtime_authorization_required",
        request=_request(),
        authorization=None,
        authorization_verified=False,
        correlation_id="route-123",
        service_version="0.4.0",
        environment="production",
        occurred_at=datetime(2026, 8, 7, 20, 1, tzinfo=UTC),
    )

    assert envelope.event.category is RuntimeViolationCategory.AUTHORIZATION
    assert envelope.event.authorization.state is RuntimeViolationAuthorizationState.ABSENT
    assert envelope.event.authorization.authorization_id is None
    assert envelope.event_digest == envelope.event.digest()
    assert "prompt" not in envelope.model_dump_json().lower()
    assert "api_key" not in envelope.model_dump_json().lower()


def test_digest_detects_event_tampering() -> None:
    envelope = build_runtime_violation(
        code="invalid_signature",
        request=_request(),
        authorization=None,
        authorization_verified=False,
        correlation_id="route-123",
        service_version="0.4.0",
        environment="production",
        occurred_at=datetime(2026, 8, 7, 20, 1, tzinfo=UTC),
    )
    payload = envelope.model_dump(mode="json")
    payload["event"]["code"] = "runtime_authorization_required"

    try:
        RuntimeViolationEnvelope.model_validate(payload)
    except ValueError:
        pass
    else:
        raise AssertionError("tampered event must fail digest validation")


def test_reason_codes_map_to_bounded_categories() -> None:
    assert violation_category("replay_detected") is RuntimeViolationCategory.REPLAY
    assert (
        violation_category("request_binding_mismatch") is RuntimeViolationCategory.REQUEST_BINDING
    )
    assert (
        violation_category("governance_policy_mismatch")
        is RuntimeViolationCategory.GOVERNANCE_PROVENANCE
    )
    assert (
        violation_category("selected_model_group_not_authorized")
        is RuntimeViolationCategory.MODEL_SCOPE
    )
    assert violation_category("invalid_signature") is RuntimeViolationCategory.AUTHORIZATION


def test_governance_fixture_is_contract_compatible() -> None:
    import json
    from pathlib import Path

    fixture = Path(__file__).resolve().parents[1] / "fixtures/governance-runtime-violation-v1.json"
    envelope = RuntimeViolationEnvelope.model_validate(
        json.loads(fixture.read_text(encoding="utf-8"))
    )

    assert (
        envelope.event_digest == "0c3d28c524625df31b9082d64cfb915d2f2ca9fc99dcf281d35a545b33826873"
    )
