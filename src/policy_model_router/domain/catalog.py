"""Declarative routing policy: model-group capabilities and the workload routing table.

Workloads and logical model groups are policy-defined identifiers. The domain does not carry a
system-wide enum of either extension point; the loaded policy defines the recognized vocabulary.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from policy_model_router.domain.enums import DataClassification, RiskLevel
from policy_model_router.domain.identifiers import ModelGroupId, WorkloadId


@dataclass(frozen=True, slots=True)
class ModelGroupProfile:
    """Capabilities and authorizations of one logical model group."""

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
    """The model group a workload maps to, and workload-level requirements."""

    model_group: ModelGroupId
    requires_tool_calling: bool


@dataclass(frozen=True, slots=True)
class RoutingPolicy:
    """Full versioned routing policy plus deterministic content provenance."""

    schema_version: str
    policy_id: str
    policy_version: str
    policy_digest: str
    model_groups: Mapping[ModelGroupId, ModelGroupProfile]
    workloads: Mapping[WorkloadId, WorkloadRule]
