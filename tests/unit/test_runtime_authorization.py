"""Tests for Governance-signed runtime authorization enforcement."""

import asyncio
import base64
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from policy_model_router.adapters.runtime_authorization import (
    InMemoryRuntimeAuthorizationReplayGuard,
)
from policy_model_router.application.runtime_authorization import (
    RuntimeAuthorizationVerifier,
    VerifiedRuntimeAuthorization,
)
from policy_model_router.application.runtime_authorization_contract import (
    AuthorizationAutonomyLevel,
    AuthorizationRiskTier,
    AuthorizedRuntimeModel,
    RuntimeAuthorizationClaims,
    RuntimeAuthorizationPolicyProvenance,
    RuntimeAuthorizationProtectedHeader,
    RuntimeAuthorizationScope,
    RuntimeAuthorizationSubject,
    RuntimeRequestBinding,
    SignedRuntimeAuthorization,
)
from policy_model_router.domain.enums import (
    DataClassification,
    ModelGroup,
    RiskLevel,
    Workload,
)
from policy_model_router.domain.routing import RouteRequest
from policy_model_router.domain.runtime_authorization import (
    RuntimeAuthorizationError,
    RuntimeAuthorizationKeyStatus,
    TrustedRuntimeAuthorizationKey,
    TrustedRuntimeAuthorizationKeySet,
)

NOW = datetime(2026, 8, 7, 18, 0, tzinfo=UTC)
AGENT_ID = UUID("33333333-3333-4333-8333-333333333333")


def _route_request(**updates: object) -> RouteRequest:
    request = RouteRequest(
        schema_version="1.0",
        requested_at=NOW,
        workflow_id="credit-analysis-2026-001",
        task_id="draft-opinion",
        agent_name="Agente de Parecer de Crédito PJ",
        workload=Workload.OPINION_DRAFTING,
        risk_level=RiskLevel.HIGH,
        data_classification=DataClassification.RESTRICTED,
        context_tokens_estimated=3000,
        max_output_tokens_estimated=900,
        structured_output_required=False,
        max_latency_ms=30_000,
        max_cost_usd=Decimal("0.30"),
    )
    return replace(request, **updates) if updates else request


def _claims(**updates: object) -> RuntimeAuthorizationClaims:
    values: dict[str, object] = {
        "authorization_id": UUID("55555555-5555-4555-8555-555555555555"),
        "issuer": "verifiable-ai-governance:production",
        "audience": ("policy-model-router",),
        "issued_at": NOW,
        "not_before": NOW,
        "expires_at": NOW + timedelta(minutes=5),
        "subject": RuntimeAuthorizationSubject(
            initiative_id=UUID("11111111-1111-4111-8111-111111111111"),
            ai_system_id=UUID("22222222-2222-4222-8222-222222222222"),
            ai_system_version=4,
            agent_id=AGENT_ID,
            agent_version=7,
            agent_review_digest="b" * 64,
        ),
        "request": RuntimeRequestBinding(
            workflow_id="credit-analysis-2026-001",
            task_id="draft-opinion",
            workload="opinion_drafting",
            context_tokens_estimated=3000,
            max_output_tokens_estimated=900,
            structured_output_required=False,
            max_latency_ms=30_000,
            max_cost_usd_micros=300_000,
        ),
        "scope": RuntimeAuthorizationScope(
            risk_tier=AuthorizationRiskTier.HIGH,
            data_classification=DataClassification.RESTRICTED,
            autonomy_level=AuthorizationAutonomyLevel.A2_PREPARE_FOR_APPROVAL,
            models=(
                AuthorizedRuntimeModel(
                    model_id=UUID("44444444-4444-4444-8444-444444444444"),
                    entity_version=2,
                    model_version="2026.08.0",
                    routing_group="reasoning-strong",
                    review_digest="a" * 64,
                    allowed_data_classes=(DataClassification.RESTRICTED,),
                ),
            ),
            allowed_tools=("policy-mcp:read",),
            permissions=("credit:analysis:read",),
            max_runtime_seconds=30,
            human_approval_points=("final-credit-approval",),
            kill_switch_enabled=True,
        ),
        "scope_digest": "c" * 64,
        "policy": RuntimeAuthorizationPolicyProvenance(
            policy_id="baseline-governance-policy",
            policy_version="1.0.0",
            policy_digest="d" * 64,
            control_catalog_id="verifiable-ai-governance-baseline",
            control_catalog_version="1.0.0",
            control_catalog_digest="e" * 64,
        ),
    }
    values.update(updates)
    return RuntimeAuthorizationClaims(**values)


def _signed(
    private_key: Ed25519PrivateKey,
    *,
    claims: RuntimeAuthorizationClaims | None = None,
) -> SignedRuntimeAuthorization:
    protected = RuntimeAuthorizationProtectedHeader(kid="gov-ed25519-2026-08")
    provisional = SignedRuntimeAuthorization(
        protected=protected,
        claims=claims or _claims(),
        signature="A" * 86,
    )
    signature = private_key.sign(provisional.signing_bytes())
    encoded = base64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")
    return provisional.model_copy(update={"signature": encoded})


def _verifier(
    private_key: Ed25519PrivateKey,
    *,
    status: RuntimeAuthorizationKeyStatus = RuntimeAuthorizationKeyStatus.ACTIVE,
    bound_agent_id: UUID = AGENT_ID,
) -> RuntimeAuthorizationVerifier:
    key = TrustedRuntimeAuthorizationKey(
        kid="gov-ed25519-2026-08",
        status=status,
        public_key=private_key.public_key(),
        not_before=NOW - timedelta(days=1),
        verify_until=NOW + timedelta(days=1),
    )
    return RuntimeAuthorizationVerifier(
        key_set=TrustedRuntimeAuthorizationKeySet(1, (key,)),
        replay_guard=InMemoryRuntimeAuthorizationReplayGuard(),
        issuer="verifiable-ai-governance:production",
        audience="policy-model-router",
        agent_bindings={"Agente de Parecer de Crédito PJ": bound_agent_id},
        expected_policy_id="baseline-governance-policy",
        expected_policy_version="1.0.0",
        expected_policy_digest="d" * 64,
        expected_control_catalog_id="verifiable-ai-governance-baseline",
        expected_control_catalog_version="1.0.0",
        expected_control_catalog_digest="e" * 64,
    )


def _verify(
    verifier: RuntimeAuthorizationVerifier,
    envelope: SignedRuntimeAuthorization,
    request: RouteRequest,
    *,
    seconds: int = 1,
) -> VerifiedRuntimeAuthorization:
    return asyncio.run(
        verifier.verify(
            envelope,
            request,
            now=NOW + timedelta(seconds=seconds),
        )
    )


def test_valid_authorization_binds_request_and_selected_group() -> None:
    private_key = Ed25519PrivateKey.generate()
    verifier = _verifier(private_key)
    verified = _verify(verifier, _signed(private_key), _route_request())

    verifier.require_selected_model(verified, ModelGroup.REASONING_STRONG)


def test_tampered_claim_fails_signature() -> None:
    private_key = Ed25519PrivateKey.generate()
    envelope = _signed(private_key)
    tampered = envelope.model_copy(
        update={"claims": envelope.claims.model_copy(update={"scope_digest": "f" * 64})}
    )

    with pytest.raises(RuntimeAuthorizationError) as exc_info:
        _verify(_verifier(private_key), tampered, _route_request())

    assert exc_info.value.code == "invalid_signature"


def test_request_budget_mismatch_fails_closed() -> None:
    private_key = Ed25519PrivateKey.generate()

    with pytest.raises(RuntimeAuthorizationError) as exc_info:
        _verify(
            _verifier(private_key),
            _signed(private_key),
            _route_request(max_latency_ms=29_999),
        )

    assert exc_info.value.code == "request_binding_mismatch"


def test_agent_name_must_bind_to_signed_agent_uuid() -> None:
    private_key = Ed25519PrivateKey.generate()

    with pytest.raises(RuntimeAuthorizationError) as exc_info:
        _verify(
            _verifier(
                private_key,
                bound_agent_id=UUID("99999999-9999-4999-8999-999999999999"),
            ),
            _signed(private_key),
            _route_request(),
        )

    assert exc_info.value.code == "agent_binding_mismatch"


def test_replay_is_rejected() -> None:
    private_key = Ed25519PrivateKey.generate()
    verifier = _verifier(private_key)
    envelope = _signed(private_key)
    request = _route_request()

    _verify(verifier, envelope, request, seconds=1)
    with pytest.raises(RuntimeAuthorizationError) as exc_info:
        _verify(verifier, envelope, request, seconds=2)

    assert exc_info.value.code == "replay_detected"


def test_revoked_key_is_rejected_before_replay() -> None:
    private_key = Ed25519PrivateKey.generate()

    with pytest.raises(RuntimeAuthorizationError) as exc_info:
        _verify(
            _verifier(
                private_key,
                status=RuntimeAuthorizationKeyStatus.REVOKED,
            ),
            _signed(private_key),
            _route_request(),
        )

    assert exc_info.value.code == "key_revoked"


def test_unexpected_governance_policy_is_rejected() -> None:
    private_key = Ed25519PrivateKey.generate()
    policy = RuntimeAuthorizationPolicyProvenance(
        policy_id="other-policy",
        policy_version="1.0.0",
        policy_digest="d" * 64,
        control_catalog_id="verifiable-ai-governance-baseline",
        control_catalog_version="1.0.0",
        control_catalog_digest="e" * 64,
    )

    with pytest.raises(RuntimeAuthorizationError) as exc_info:
        _verify(
            _verifier(private_key),
            _signed(private_key, claims=_claims(policy=policy)),
            _route_request(),
        )

    assert exc_info.value.code == "governance_policy_mismatch"


def test_selected_group_must_be_in_signed_model_allowlist() -> None:
    private_key = Ed25519PrivateKey.generate()
    verifier = _verifier(private_key)
    verified = _verify(verifier, _signed(private_key), _route_request())

    with pytest.raises(RuntimeAuthorizationError) as exc_info:
        verifier.require_selected_model(verified, ModelGroup.REASONING_MEDIUM)

    assert exc_info.value.code == "selected_model_group_not_authorized"


def test_wrong_audience_is_rejected() -> None:
    private_key = Ed25519PrivateKey.generate()

    with pytest.raises(RuntimeAuthorizationError) as exc_info:
        _verify(
            _verifier(private_key),
            _signed(
                private_key,
                claims=_claims(audience=("multi-agent-credit-desk",)),
            ),
            _route_request(),
        )

    assert exc_info.value.code == "audience_mismatch"


def test_governance_contract_fixture_keeps_p11_signing_digest() -> None:
    fixture_path = (
        Path(__file__).resolve().parents[1] / "fixtures/governance-runtime-authorization-v1.json"
    )
    envelope = SignedRuntimeAuthorization.model_validate(
        json.loads(fixture_path.read_text(encoding="utf-8"))
    )

    assert (
        envelope.signing_digest()
        == "0d13e7145f7134df320db25833e2656de2f321ad538bbf36b503cd60e24760df"
    )
