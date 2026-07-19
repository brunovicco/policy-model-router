"""Domain Value Objects and errors for model-routing decisions.

These mirror the shape of ``credit_desk_contracts.routing`` from the ``multi-agent-credit-desk``
monorepo, translated into framework-free domain types. Entrypoints map the external (Pydantic)
wire contract into these types before invoking the application use case.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from policy_model_router.domain.enums import DataClassification, ModelGroup, RiskLevel, Workload


@dataclass(frozen=True, slots=True)
class RouteRequest:
    """A model-routing request submitted by an agent before an LLM call."""

    schema_version: str
    requested_at: datetime
    workflow_id: str
    task_id: str
    agent_name: str
    workload: Workload
    risk_level: RiskLevel
    data_classification: DataClassification
    context_tokens_estimated: int
    structured_output_required: bool
    max_latency_ms: int
    max_cost_usd: Decimal


@dataclass(frozen=True, slots=True)
class RejectedCandidate:
    """One model group excluded from a routing decision, with the eliminating reason."""

    model_group: ModelGroup
    reason: str


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """The outcome of a model-routing decision, including every rejected candidate."""

    schema_version: str
    routing_decision_id: str
    decided_at: datetime
    workflow_id: str
    task_id: str
    selected_model_group: ModelGroup
    reason: str
    rejected_candidates: tuple[RejectedCandidate, ...]


class NoViableModelGroupError(Exception):
    """Raised when the workload's mapped model group fails an eliminatory constraint.

    The MVP router (ADR-0005) has no weighted-score fallback to reroute to a different group -
    that is deferred to Phase 3, once per-workload evaluation data exists. A request that cannot
    be routed is a hard failure the caller must handle, not a silent reroute.
    """

    def __init__(self, workload: Workload, model_group: ModelGroup, reason: str) -> None:
        """Record the workload, its mapped (and rejected) model group, and the reason."""
        super().__init__(
            f"no viable model group for workload {workload.value!r}: "
            f"mapped group {model_group.value!r} rejected ({reason})"
        )
        self.workload = workload
        self.model_group = model_group
        self.reason = reason
