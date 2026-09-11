"""External wire contract for versioned model-routing requests and decisions.

API schema 1.0 keeps data classification and risk level as controlled vocabularies while accepting
policy-defined workload and logical model-group identifiers. Unknown but syntactically valid
workloads therefore reach the deterministic policy boundary and fail closed when absent from the
active policy instead of being hard-coded into the transport schema.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidateAs,
)

from policy_model_router.application.runtime_authorization_contract import (
    SignedRuntimeAuthorization,
)
from policy_model_router.domain.enums import DataClassification, ReasonCode, RiskLevel
from policy_model_router.domain.identifiers import (
    POLICY_IDENTIFIER_PATTERN,
    ModelGroupId,
    WorkloadId,
)
from policy_model_router.domain.routing import RejectedDecision as DomainRejectedDecision
from policy_model_router.domain.routing import RouteDecision as DomainRouteDecision
from policy_model_router.domain.routing import RouteRequest as DomainRouteRequest


def _require_utc(value: datetime) -> datetime:
    """Reject naive datetimes and datetimes not expressed in UTC."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware and expressed in UTC")
    return value


UtcDatetime = Annotated[datetime, AfterValidator(_require_utc)]
_NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]
_BoundedIdentifier = Annotated[str, StringConstraints(min_length=1, max_length=200)]
_PolicyIdentifierText = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=POLICY_IDENTIFIER_PATTERN),
]
_WorkloadField = Annotated[WorkloadId, ValidateAs(_PolicyIdentifierText, WorkloadId)]
_ModelGroupField = Annotated[ModelGroupId, ValidateAs(_PolicyIdentifierText, ModelGroupId)]


class StrictContract(BaseModel):
    """Immutable, closed external contract base."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelRouteRequest(StrictContract):
    """A model-routing request submitted by an agent before an LLM call."""

    schema_version: Literal["1.0"]
    requested_at: UtcDatetime
    workflow_id: _BoundedIdentifier
    task_id: _BoundedIdentifier
    agent_name: _BoundedIdentifier
    workload: _WorkloadField
    risk_level: RiskLevel
    data_classification: DataClassification
    context_tokens_estimated: Annotated[int, Field(ge=0, le=10_000_000)]
    max_output_tokens_estimated: Annotated[int, Field(ge=0, le=10_000_000)]
    structured_output_required: bool
    max_latency_ms: Annotated[int, Field(gt=0)]
    max_cost_usd: Annotated[Decimal, Field(gt=0)]
    include_rejected_candidates: bool = True
    """Whether the decision should explain the groups that were not selected.

    Defaults to true, so an existing caller that omits it is unaffected. Opting out evaluates only
    the workload's mapped group rather than the whole catalog; the selected group and the
    accept/reject outcome are identical either way, and ``rejected_candidates`` comes back as an
    empty list rather than disappearing, so the response shape is unchanged.

    This field is deliberately absent from the signed Governance request binding: it changes how
    much of the decision is explained, never what the decision is, so a caller can set it without
    invalidating an authorization signed for that request.
    """


class AuthorizedModelRouteRequest(StrictContract):
    """Route body carrying the request plus Governance authorization."""

    request: ModelRouteRequest
    authorization: SignedRuntimeAuthorization


RouteRequestEnvelope = ModelRouteRequest | AuthorizedModelRouteRequest


class RejectedCandidate(StrictContract):
    """One model group excluded from a routing decision, with the eliminating reason."""

    model_group: _ModelGroupField
    reason: _NonEmptyStr
    reason_code: ReasonCode
    observed_value: _NonEmptyStr
    required_value: _NonEmptyStr


class ModelRouteDecision(StrictContract):
    """The outcome of a model-routing decision, including every rejected candidate."""

    schema_version: Literal["1.0"]
    routing_decision_id: _NonEmptyStr
    decided_at: UtcDatetime
    workflow_id: _NonEmptyStr
    task_id: _NonEmptyStr
    selected_model_group: _ModelGroupField
    reason: _NonEmptyStr
    rejected_candidates: tuple[RejectedCandidate, ...]
    policy_id: _NonEmptyStr
    policy_version: _NonEmptyStr
    policy_digest: _NonEmptyStr
    service_version: _NonEmptyStr
    environment: _NonEmptyStr


class RouteRejection(StrictContract):
    """Hard routing rejection with policy and deployment provenance."""

    schema_version: Literal["1.0"]
    routing_decision_id: _NonEmptyStr
    decided_at: UtcDatetime
    workflow_id: _NonEmptyStr
    task_id: _NonEmptyStr
    workload: _WorkloadField
    rejected_model_group: _ModelGroupField
    reason: _NonEmptyStr
    reason_code: ReasonCode
    observed_value: _NonEmptyStr
    required_value: _NonEmptyStr
    policy_id: _NonEmptyStr
    policy_version: _NonEmptyStr
    policy_digest: _NonEmptyStr
    service_version: _NonEmptyStr
    environment: _NonEmptyStr


def to_domain_request(request: ModelRouteRequest) -> DomainRouteRequest:
    """Map the validated wire request into the framework-free domain request."""
    return DomainRouteRequest(
        schema_version=request.schema_version,
        requested_at=request.requested_at,
        workflow_id=request.workflow_id,
        task_id=request.task_id,
        agent_name=request.agent_name,
        workload=request.workload,
        risk_level=request.risk_level,
        data_classification=request.data_classification,
        context_tokens_estimated=request.context_tokens_estimated,
        max_output_tokens_estimated=request.max_output_tokens_estimated,
        structured_output_required=request.structured_output_required,
        max_latency_ms=request.max_latency_ms,
        max_cost_usd=request.max_cost_usd,
    )


def from_domain_decision(decision: DomainRouteDecision) -> ModelRouteDecision:
    """Map a domain routing decision into the wire response contract."""
    return ModelRouteDecision(
        schema_version=decision.schema_version,
        routing_decision_id=decision.routing_decision_id,
        decided_at=decision.decided_at,
        workflow_id=decision.workflow_id,
        task_id=decision.task_id,
        selected_model_group=decision.selected_model_group,
        reason=decision.reason,
        rejected_candidates=tuple(
            RejectedCandidate(
                model_group=candidate.model_group,
                reason=candidate.reason,
                reason_code=candidate.reason_code,
                observed_value=candidate.observed_value,
                required_value=candidate.required_value,
            )
            for candidate in decision.rejected_candidates
        ),
        policy_id=decision.policy_id,
        policy_version=decision.policy_version,
        policy_digest=decision.policy_digest,
        service_version=decision.service_version,
        environment=decision.environment,
    )


def from_domain_rejection(decision: DomainRejectedDecision) -> RouteRejection:
    """Map a domain rejected decision into the wire response contract."""
    return RouteRejection(
        schema_version=decision.schema_version,
        routing_decision_id=decision.routing_decision_id,
        decided_at=decision.decided_at,
        workflow_id=decision.workflow_id,
        task_id=decision.task_id,
        workload=decision.workload,
        rejected_model_group=decision.rejected_model_group,
        reason=decision.reason,
        reason_code=decision.reason_code,
        observed_value=decision.observed_value,
        required_value=decision.required_value,
        policy_id=decision.policy_id,
        policy_version=decision.policy_version,
        policy_digest=decision.policy_digest,
        service_version=decision.service_version,
        environment=decision.environment,
    )


__all__ = [
    "AuthorizedModelRouteRequest",
    "ModelRouteDecision",
    "ModelRouteRequest",
    "RejectedCandidate",
    "RouteRejection",
    "RouteRequestEnvelope",
    "StrictContract",
    "UtcDatetime",
    "from_domain_decision",
    "from_domain_rejection",
    "to_domain_request",
]
