"""Build content-minimized violation evidence from Router enforcement failures."""

from datetime import UTC, datetime
from uuid import uuid4

from policy_model_router.entrypoints.contracts import ModelRouteRequest
from policy_model_router.runtime_authorization_contract import SignedRuntimeAuthorization
from policy_model_router.runtime_violation_contract import (
    RuntimeViolationAuthorizationContext,
    RuntimeViolationAuthorizationState,
    RuntimeViolationCategory,
    RuntimeViolationEnvelope,
    RuntimeViolationEvent,
    RuntimeViolationRequestContext,
)

_REPLAY_CODES = frozenset(
    {
        "replay_detected",
        "replay_store_full",
        "replay_store_unavailable",
    }
)
_REQUEST_BINDING_CODES = frozenset(
    {
        "request_cost_precision_unsupported",
        "request_binding_mismatch",
        "agent_binding_mismatch",
    }
)
_PROVENANCE_CODES = frozenset({"governance_policy_mismatch"})
_MODEL_SCOPE_CODES = frozenset(
    {
        "selected_model_group_not_authorized",
        "selected_model_data_class_not_authorized",
    }
)


def violation_category(code: str) -> RuntimeViolationCategory:
    """Map the bounded runtime-authorization reason code to one stable category."""
    if code in _REPLAY_CODES:
        return RuntimeViolationCategory.REPLAY
    if code in _REQUEST_BINDING_CODES:
        return RuntimeViolationCategory.REQUEST_BINDING
    if code in _PROVENANCE_CODES:
        return RuntimeViolationCategory.GOVERNANCE_PROVENANCE
    if code in _MODEL_SCOPE_CODES:
        return RuntimeViolationCategory.MODEL_SCOPE
    return RuntimeViolationCategory.AUTHORIZATION


def build_runtime_violation(
    *,
    code: str,
    request: ModelRouteRequest,
    authorization: SignedRuntimeAuthorization | None,
    authorization_verified: bool,
    correlation_id: str,
    service_version: str,
    environment: str,
    selected_model_group: str | None = None,
    occurred_at: datetime | None = None,
) -> RuntimeViolationEnvelope:
    """Create one safe violation event without request content or credentials."""
    timestamp = occurred_at or datetime.now(UTC)
    auth_context = _authorization_context(
        authorization,
        verified=authorization_verified,
    )
    event = RuntimeViolationEvent(
        event_id=uuid4(),
        occurred_at=timestamp,
        service_version=service_version,
        environment=environment,
        correlation_id=correlation_id,
        category=violation_category(code),
        code=code,
        request=RuntimeViolationRequestContext(
            workflow_id=request.workflow_id,
            task_id=request.task_id,
            agent_name=request.agent_name,
            workload=request.workload.value,
        ),
        authorization=auth_context,
        selected_model_group=selected_model_group,
    )
    return RuntimeViolationEnvelope.from_event(event)


def _authorization_context(
    authorization: SignedRuntimeAuthorization | None,
    *,
    verified: bool,
) -> RuntimeViolationAuthorizationContext:
    """Expose structural identifiers only; never copy prompts, headers, or secrets."""
    if authorization is None:
        return RuntimeViolationAuthorizationContext(state=RuntimeViolationAuthorizationState.ABSENT)
    return RuntimeViolationAuthorizationContext(
        state=(
            RuntimeViolationAuthorizationState.VERIFIED
            if verified
            else RuntimeViolationAuthorizationState.PRESENT
        ),
        authorization_id=authorization.claims.authorization_id,
        key_id=authorization.protected.kid,
        signing_digest=authorization.signing_digest(),
        scope_digest=authorization.claims.scope_digest,
    )
