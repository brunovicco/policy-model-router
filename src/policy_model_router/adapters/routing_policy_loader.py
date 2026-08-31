"""Load and validate the declarative routing policy from versioned YAML.

Workload and logical model-group names are policy-defined validated identifiers. The loader fails
closed on malformed structure, invalid identifiers, duplicate keys, empty catalogs, or references
to model groups that are not declared in the same policy.
"""

import hashlib
import types
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal

import pydantic
import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidateAs, model_validator

from policy_model_router.domain.catalog import ModelGroupProfile, RoutingPolicy, WorkloadRule
from policy_model_router.domain.enums import DataClassification, RiskLevel
from policy_model_router.domain.identifiers import (
    POLICY_IDENTIFIER_PATTERN,
    ModelGroupId,
    WorkloadId,
)


class RoutingPolicyLoadError(RuntimeError):
    """Raised when a routing policy cannot be read into a valid fail-closed domain policy."""


class _NoDuplicateKeysLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate mapping keys instead of silently overwriting them."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[object, object]:
        seen: set[object] = set()
        for key_node, _value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"found duplicate key {key!r}",
                    key_node.start_mark,
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


_IdentifierText = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=POLICY_IDENTIFIER_PATTERN),
]
_ModelGroupField = Annotated[ModelGroupId, ValidateAs(_IdentifierText, ModelGroupId)]
_WorkloadField = Annotated[WorkloadId, ValidateAs(_IdentifierText, WorkloadId)]


class _ModelGroupProfileConfig(BaseModel):
    """Validated YAML shape of one model group's capabilities and authorizations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    authorized_data_classifications: Annotated[list[DataClassification], Field(min_length=1)]
    authorized_risk_levels: Annotated[list[RiskLevel], Field(min_length=1)]
    supports_structured_output: bool
    supports_tool_calling: bool
    max_context_tokens: Annotated[int, Field(gt=0)]
    typical_latency_ms: Annotated[int, Field(gt=0)]
    input_cost_usd_per_million_tokens: Annotated[Decimal, Field(ge=0)]
    output_cost_usd_per_million_tokens: Annotated[Decimal, Field(ge=0)]
    available: bool
    allowed_agents: list[str]


class _WorkloadRuleConfig(BaseModel):
    """Validated YAML shape of one policy-defined workload rule."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    model_group: _ModelGroupField
    requires_tool_calling: bool


class _RoutingPolicyConfig(BaseModel):
    """Validated YAML shape of the whole routing policy file."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    policy_id: Annotated[str, Field(min_length=1)]
    policy_version: Annotated[str, Field(min_length=1)]
    model_groups: Annotated[dict[_ModelGroupField, _ModelGroupProfileConfig], Field(min_length=1)]
    workloads: Annotated[dict[_WorkloadField, _WorkloadRuleConfig], Field(min_length=1)]

    @model_validator(mode="after")
    def _require_referential_integrity(self) -> "_RoutingPolicyConfig":
        """Require every workload mapping to reference a model group declared in this policy."""
        undefined = {
            rule.model_group
            for rule in self.workloads.values()
            if rule.model_group not in self.model_groups
        }
        if undefined:
            names = ", ".join(sorted(group.value for group in undefined))
            raise ValueError(f"workloads reference undefined model groups: {names}")

        referenced = {rule.model_group for rule in self.workloads.values()}
        unreferenced = set(self.model_groups) - referenced
        if unreferenced:
            names = ", ".join(sorted(group.value for group in unreferenced))
            raise ValueError(f"model_groups contains unreachable entries: {names}")
        return self


def _to_domain(config: _RoutingPolicyConfig, *, policy_digest: str) -> RoutingPolicy:
    model_groups: Mapping[ModelGroupId, ModelGroupProfile] = types.MappingProxyType(
        {
            group: ModelGroupProfile(
                authorized_data_classifications=frozenset(profile.authorized_data_classifications),
                authorized_risk_levels=frozenset(profile.authorized_risk_levels),
                supports_structured_output=profile.supports_structured_output,
                supports_tool_calling=profile.supports_tool_calling,
                max_context_tokens=profile.max_context_tokens,
                typical_latency_ms=profile.typical_latency_ms,
                input_cost_usd_per_million_tokens=profile.input_cost_usd_per_million_tokens,
                output_cost_usd_per_million_tokens=profile.output_cost_usd_per_million_tokens,
                available=profile.available,
                allowed_agents=frozenset(profile.allowed_agents),
            )
            for group, profile in config.model_groups.items()
        }
    )
    workloads: Mapping[WorkloadId, WorkloadRule] = types.MappingProxyType(
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
        policy_id=config.policy_id,
        policy_version=config.policy_version,
        policy_digest=policy_digest,
        model_groups=model_groups,
        workloads=workloads,
    )


def load_routing_policy(path: Path) -> RoutingPolicy:
    """Read, validate, and convert the routing policy YAML file at ``path``."""
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RoutingPolicyLoadError(f"cannot read routing policy file {path}: {exc}") from exc

    try:
        # _NoDuplicateKeysLoader subclasses SafeLoader and adds no unsafe constructors; it only
        # rejects duplicate mapping keys. Static scanners cannot infer that custom-loader property.
        raw_data = yaml.load(raw_text, Loader=_NoDuplicateKeysLoader)  # noqa: S506  # nosec B506
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

    policy_digest = f"sha256:{hashlib.sha256(raw_text.encode('utf-8')).hexdigest()}"
    return _to_domain(config, policy_digest=policy_digest)
