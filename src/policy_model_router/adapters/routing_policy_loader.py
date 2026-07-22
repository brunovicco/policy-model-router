"""Loads the declarative routing policy from a versioned YAML file.

Fails closed: any structural problem (missing file, malformed YAML, unknown or missing keys,
incomplete catalog/workload coverage) raises :class:`RoutingPolicyLoadError` rather than falling
back to a partial or default policy.
"""

import types
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Annotated

import pydantic
import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from policy_model_router.domain.catalog import ModelGroupProfile, RoutingPolicy, WorkloadRule
from policy_model_router.domain.enums import DataClassification, ModelGroup, RiskLevel, Workload


class RoutingPolicyLoadError(RuntimeError):
    """Raised when the routing policy file cannot be read into a valid ``RoutingPolicy``."""


class _ModelGroupProfileConfig(BaseModel):
    """Validated YAML shape of one model group's capabilities and authorizations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    authorized_data_classifications: Annotated[list[DataClassification], Field(min_length=1)]
    authorized_risk_levels: Annotated[list[RiskLevel], Field(min_length=1)]
    supports_structured_output: bool
    supports_tool_calling: bool
    max_context_tokens: Annotated[int, Field(gt=0)]
    typical_latency_ms: Annotated[int, Field(gt=0)]
    estimated_cost_usd: Annotated[Decimal, Field(gt=0)]
    available: bool
    allowed_agents: list[str]


class _WorkloadRuleConfig(BaseModel):
    """Validated YAML shape of one workload's routing rule."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_group: ModelGroup
    requires_tool_calling: bool


class _RoutingPolicyConfig(BaseModel):
    """Validated YAML shape of the whole routing policy file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    model_groups: dict[ModelGroup, _ModelGroupProfileConfig]
    workloads: dict[Workload, _WorkloadRuleConfig]

    @model_validator(mode="after")
    def _require_full_coverage(self) -> "_RoutingPolicyConfig":
        """Require every declared model group and workload to be covered."""
        missing_groups = set(ModelGroup) - self.model_groups.keys()
        if missing_groups:
            names = ", ".join(sorted(group.value for group in missing_groups))
            raise ValueError(f"model_groups is missing required entries: {names}")
        missing_workloads = set(Workload) - self.workloads.keys()
        if missing_workloads:
            names = ", ".join(sorted(workload.value for workload in missing_workloads))
            raise ValueError(f"workloads is missing required entries: {names}")
        return self


def _to_domain(config: _RoutingPolicyConfig) -> RoutingPolicy:
    model_groups: Mapping[ModelGroup, ModelGroupProfile] = types.MappingProxyType(
        {
            group: ModelGroupProfile(
                authorized_data_classifications=frozenset(profile.authorized_data_classifications),
                authorized_risk_levels=frozenset(profile.authorized_risk_levels),
                supports_structured_output=profile.supports_structured_output,
                supports_tool_calling=profile.supports_tool_calling,
                max_context_tokens=profile.max_context_tokens,
                typical_latency_ms=profile.typical_latency_ms,
                estimated_cost_usd=profile.estimated_cost_usd,
                available=profile.available,
                allowed_agents=frozenset(profile.allowed_agents),
            )
            for group, profile in config.model_groups.items()
        }
    )
    workloads: Mapping[Workload, WorkloadRule] = types.MappingProxyType(
        {
            workload: WorkloadRule(
                model_group=rule.model_group,
                requires_tool_calling=rule.requires_tool_calling,
            )
            for workload, rule in config.workloads.items()
        }
    )
    return RoutingPolicy(
        schema_version=config.schema_version,
        model_groups=model_groups,
        workloads=workloads,
    )


def load_routing_policy(path: Path) -> RoutingPolicy:
    """Read, validate, and convert the routing policy YAML file at ``path``.

    Raises:
        RoutingPolicyLoadError: If the file is missing or unreadable, the YAML is malformed, the
            structure doesn't match the expected schema, or the catalog/workload table is
            incomplete.
    """
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RoutingPolicyLoadError(f"cannot read routing policy file {path}: {exc}") from exc

    try:
        raw_data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise RoutingPolicyLoadError(
            f"routing policy file {path} is not valid YAML: {exc}"
        ) from exc

    try:
        config = _RoutingPolicyConfig.model_validate(raw_data)
    except pydantic.ValidationError as exc:
        raise RoutingPolicyLoadError(
            f"routing policy file {path} does not match the expected schema: {exc}"
        ) from exc

    return _to_domain(config)
