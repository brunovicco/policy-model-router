"""External wire contract for model-routing requests and decisions.

Originally mirrored ``credit_desk_contracts.routing`` / ``credit_desk_contracts.enums`` from the
``multi-agent-credit-desk`` monorepo field-for-field, including the ``StrictContract`` base
(``extra="forbid"``, immutable, strict) and the UTC-only datetime validator - but this repo does
not depend on that monorepo (separate repository, no shared package, no automated contract test
between them). ``max_output_tokens_estimated`` on the request and the ``policy_id``/
``policy_version``/``policy_digest``/``service_version``/``environment`` fields on the response are
a deliberate, documented divergence from that mirror (added for token-based cost estimation and
decision auditability, respectively): ``credit_desk_contracts`` needs updating to match before any
consumer there can rely on them. Entrypoints validate external input against these schemas and
translate it into the framework-free domain types in :mod:`policy_model_router.domain.routing`
before calling the application use case.
"""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from policy_model_router.domain.enums import (
    DataClassification,
    ModelGroup,
    ReasonCode,
    RiskLevel,
    Workload,
)
from policy_model_router.domain.routing import RouteDecision as DomainRouteDecision
from policy_model_router.domain.routing import RouteRequest as DomainRouteRequest


def _require_utc(value: datetime) -> datetime:
    """Reject naive datetimes and datetimes not expressed in UTC."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be timezone-aware and expressed in UTC")
    return value


UtcDatetime = Annotated[datetime, AfterValidator(_require_utc)]
_NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]


class StrictContract(BaseModel):
    """Base model for every wire contract here: immutable and closed to unknown fields.

    Unlike ``credit_desk_contracts._base.StrictContract``, this omits ``strict=True``: FastAPI
    validates request bodies against the already-JSON-decoded Python dict (str/float/dict), not
    the raw JSON bytes, so a strict model would reject perfectly valid JSON representations of
    datetimes, enums, and decimals. Field shapes and ``extra="forbid"``/immutability still match
    the mirrored contract exactly.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelRouteRequest(StrictContract):
    """A model-routing request submitted by an agent before an LLM call."""

    schema_version: Literal["1.0"]
    requested_at: UtcDatetime
    workflow_id: _NonEmptyStr
    task_id: _NonEmptyStr
    agent_name: _NonEmptyStr
    workload: Workload
    risk_level: RiskLevel
    data_classification: DataClassification
    context_tokens_estimated: Annotated[int, Field(ge=0)]
    max_output_tokens_estimated: Annotated[int, Field(ge=0)]
    structured_output_required: bool
    max_latency_ms: Annotated[int, Field(gt=0)]
    max_cost_usd: Annotated[Decimal, Field(gt=0)]


class RejectedCandidate(StrictContract):
    """One model group excluded from a routing decision, with the eliminating reason."""

    model_group: ModelGroup
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
    selected_model_group: ModelGroup
    reason: _NonEmptyStr
    rejected_candidates: tuple[RejectedCandidate, ...]
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


__all__ = [
    "ModelRouteDecision",
    "ModelRouteRequest",
    "RejectedCandidate",
    "StrictContract",
    "UtcDatetime",
    "from_domain_decision",
    "to_domain_request",
]
