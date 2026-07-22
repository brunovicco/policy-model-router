"""Declarative routing policy: model-group capabilities and the workload routing table.

Loaded from ``config/routing_policy.yaml`` by
:mod:`policy_model_router.adapters.routing_policy_loader` and passed into the application use
case. Kept as plain, framework-free domain Value Objects so the policy can be constructed
directly in tests without touching YAML or the filesystem.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from policy_model_router.domain.enums import DataClassification, ModelGroup, RiskLevel, Workload


@dataclass(frozen=True, slots=True)
class ModelGroupProfile:
    """Capabilities and authorizations of one model group, independent of provider/deployment.

    ``authorized_data_classifications`` is the router's half of the hard rule from
    ``docs/architecture-blueprint.md`` section 2.3: a model group is only authorized for a data
    classification if every deployment in its provider pool (as configured for this environment)
    is cleared for that classification. Provider/deployment selection itself is LiteLLM's job
    (ADR-0004), not this router's.

    ``authorized_risk_levels`` is a separate, policy-level authorization: the highest-stakes
    workflow risk tier this group may serve, independent of data classification. See ADR-0005's
    amendment for the rationale (weaker/cheaper groups are not authorized for high-stakes
    decisions, regardless of the data classification involved).

    Attributes:
        allowed_agents: Agent names allowed to use this group; empty means no restriction.
    """

    authorized_data_classifications: frozenset[DataClassification]
    authorized_risk_levels: frozenset[RiskLevel]
    supports_structured_output: bool
    supports_tool_calling: bool
    max_context_tokens: int
    typical_latency_ms: int
    estimated_cost_usd: Decimal
    available: bool
    allowed_agents: frozenset[str]


@dataclass(frozen=True, slots=True)
class WorkloadRule:
    """The model group a workload maps to, and any workload-level requirements."""

    model_group: ModelGroup
    requires_tool_calling: bool


@dataclass(frozen=True, slots=True)
class RoutingPolicy:
    """The full declarative routing policy: model-group catalog plus the workload table."""

    schema_version: str
    model_groups: Mapping[ModelGroup, ModelGroupProfile]
    workloads: Mapping[Workload, WorkloadRule]
