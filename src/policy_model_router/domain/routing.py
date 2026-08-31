"""Domain value objects and errors for deterministic model-routing decisions."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from policy_model_router.domain.enums import DataClassification, ReasonCode, RiskLevel
from policy_model_router.domain.identifiers import ModelGroupId, WorkloadId


@dataclass(frozen=True, slots=True)
class RouteRequest:
    """A model-routing request submitted by an agent before an LLM call."""

    schema_version: str
    requested_at: datetime
    workflow_id: str
    task_id: str
    agent_name: str
    workload: WorkloadId
    risk_level: RiskLevel
    data_classification: DataClassification
    context_tokens_estimated: int
    max_output_tokens_estimated: int
    structured_output_required: bool
    max_latency_ms: int
    max_cost_usd: Decimal


@dataclass(frozen=True, slots=True)
class RejectedCandidate:
    """One model group excluded from a routing decision, with the eliminating reason."""

    model_group: ModelGroupId
    reason: str
    reason_code: ReasonCode
    observed_value: str
    required_value: str


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """Successful routing outcome with policy/deployment provenance."""

    schema_version: str
    routing_decision_id: str
    decided_at: datetime
    workflow_id: str
    task_id: str
    selected_model_group: ModelGroupId
    reason: str
    rejected_candidates: tuple[RejectedCandidate, ...]
    policy_id: str
    policy_version: str
    policy_digest: str
    service_version: str
    environment: str


@dataclass(frozen=True, slots=True)
class RejectedDecision:
    """Hard routing rejection with the same provenance carried by a successful decision."""

    schema_version: str
    routing_decision_id: str
    decided_at: datetime
    workflow_id: str
    task_id: str
    workload: WorkloadId
    rejected_model_group: ModelGroupId
    reason: str
    reason_code: ReasonCode
    observed_value: str
    required_value: str
    policy_id: str
    policy_version: str
    policy_digest: str
    service_version: str
    environment: str


class NoViableModelGroupError(Exception):
    """Raised when the workload's mapped model group fails an eliminatory constraint."""

    def __init__(self, decision: RejectedDecision) -> None:
        """Record the full rejected decision, so it is exactly as auditable as an acceptance."""
        super().__init__(
            f"no viable model group for workload {decision.workload.value!r}: "
            f"mapped group {decision.rejected_model_group.value!r} rejected ({decision.reason})"
        )
        self.decision = decision
