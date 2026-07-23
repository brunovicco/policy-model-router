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
        input_cost_usd_per_million_tokens: Price of input/prompt tokens, per million tokens.
        output_cost_usd_per_million_tokens: Price of output/completion tokens, per million tokens.
    """

    authorized_data_classifications: frozenset[DataClassification]
    authorized_risk_levels: frozenset[RiskLevel]
    supports_structured_output: bool
    supports_tool_calling: bool
    max_context_tokens: int
    typical_latency_ms: int
    input_cost_usd_per_million_tokens: Decimal
    output_cost_usd_per_million_tokens: Decimal
    available: bool
    allowed_agents: frozenset[str]

    def estimated_cost(self, *, input_tokens: int, output_tokens: int) -> Decimal:
        """Return the estimated USD cost of a call with the given input/output token counts."""
        million = Decimal(1_000_000)
        return (
            Decimal(input_tokens) * self.input_cost_usd_per_million_tokens / million
            + Decimal(output_tokens) * self.output_cost_usd_per_million_tokens / million
        )


@dataclass(frozen=True, slots=True)
class WorkloadRule:
    """The model group a workload maps to, and any workload-level requirements."""

    model_group: ModelGroup
    requires_tool_calling: bool


@dataclass(frozen=True, slots=True)
class RoutingPolicy:
    """The full declarative routing policy: model-group catalog plus the workload table.

    ``policy_id``/``policy_version`` are declared in the YAML file itself and identify *which*
    policy is loaded; ``policy_digest`` is computed by the loader from the file's decoded text
    content (``sha256:<hex>``; line endings are normalized by Python's text-mode read, so CRLF and
    LF variants of the same content hash identically) and identifies *exactly what content* was
    loaded, independent of whether
    the author remembered to bump ``policy_version``. Both travel into every
    :class:`~policy_model_router.domain.routing.RouteDecision` so a decision is traceable back to
    the policy that produced it.
    """

    schema_version: str
    policy_id: str
    policy_version: str
    policy_digest: str
    model_groups: Mapping[ModelGroup, ModelGroupProfile]
    workloads: Mapping[Workload, WorkloadRule]
