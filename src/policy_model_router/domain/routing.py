"""Domain Value Objects and errors for model-routing decisions.

These mirror the shape of ``credit_desk_contracts.routing`` from the ``multi-agent-credit-desk``
monorepo, translated into framework-free domain types. Entrypoints map the external (Pydantic)
wire contract into these types before invoking the application use case.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from policy_model_router.domain.enums import (
    DataClassification,
    ModelGroup,
    ReasonCode,
    RiskLevel,
    Workload,
)


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
    max_output_tokens_estimated: int
    structured_output_required: bool
    max_latency_ms: int
    max_cost_usd: Decimal


@dataclass(frozen=True, slots=True)
class RejectedCandidate:
    """One model group excluded from a routing decision, with the eliminating reason.

    ``reason`` is the existing human-readable text; ``reason_code``/``observed_value``/
    ``required_value`` are the machine-readable form of the same rejection, so a caller doesn't
    have to parse ``reason`` to build an audit trail or a UI (see
    ``domain/constraints.py::ConstraintFailure``, which produces all four together for every
    constraint except the "mapped elsewhere" case, which this module constructs directly).
    """

    model_group: ModelGroup
    reason: str
    reason_code: ReasonCode
    observed_value: str
    required_value: str


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """The outcome of a model-routing decision, including every rejected candidate.

    ``policy_id``/``policy_version``/``policy_digest`` identify the loaded routing policy that
    produced this decision (see ``domain/catalog.py::RoutingPolicy``); ``service_version`` and
    ``environment`` identify the deployment that produced it. Together they make a decision
    reproducible and auditable: two decisions can only be assumed equivalent if all five match.
    """

    schema_version: str
    routing_decision_id: str
    decided_at: datetime
    workflow_id: str
    task_id: str
    selected_model_group: ModelGroup
    reason: str
    rejected_candidates: tuple[RejectedCandidate, ...]
    policy_id: str
    policy_version: str
    policy_digest: str
    service_version: str
    environment: str


@dataclass(frozen=True, slots=True)
class RejectedDecision:
    """The outcome of a routing decision whose mapped model group failed a hard constraint.

    Carries the same five identity fields (``policy_id``/``policy_version``/``policy_digest``/
    ``service_version``/``environment``) plus ``routing_decision_id``/``decided_at`` as a
    successful :class:`RouteDecision`, so a rejection is exactly as auditable as an acceptance -
    only the outcome-specific fields (``rejected_model_group``, ``reason``, ``reason_code``,
    ``observed_value``, ``required_value``) differ.
    """

    schema_version: str
    routing_decision_id: str
    decided_at: datetime
    workflow_id: str
    task_id: str
    workload: Workload
    rejected_model_group: ModelGroup
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
    """Raised when the workload's mapped model group fails an eliminatory constraint.

    The MVP router (ADR-0005) has no weighted-score fallback to reroute to a different group -
    that is deferred to Phase 3, once per-workload evaluation data exists. A request that cannot
    be routed is a hard failure the caller must handle, not a silent reroute.
    """

    def __init__(self, decision: RejectedDecision) -> None:
        """Record the full rejected decision, so it is exactly as auditable as an acceptance."""
        super().__init__(
            f"no viable model group for workload {decision.workload.value!r}: "
            f"mapped group {decision.rejected_model_group.value!r} rejected ({decision.reason})"
        )
        self.decision = decision
