"""Tests for loading and validating the declarative routing policy.

Includes a golden-file check against the shipped ``config/routing_policy.yaml`` (a checked-in,
versioned config, not an external or nondeterministic filesystem) alongside pure failure-path
tests driven by ``tmp_path``.
"""

from pathlib import Path

import pytest

from policy_model_router.adapters.routing_policy_loader import (
    RoutingPolicyLoadError,
    load_routing_policy,
)
from policy_model_router.domain.enums import DataClassification, ModelGroup, Workload

_SHIPPED_POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "routing_policy.yaml"

_VALID_YAML = """
schema_version: "1.0"
policy_id: "test-policy"
policy_version: "1.0.0"
model_groups:
  fast-small:
    authorized_data_classifications: [public]
    authorized_risk_levels: [low, medium, high, critical]
    supports_structured_output: false
    supports_tool_calling: true
    max_context_tokens: 1000
    typical_latency_ms: 1000
    input_cost_usd_per_million_tokens: "0.10"
    output_cost_usd_per_million_tokens: "0.40"
    available: true
    allowed_agents: []
  reasoning-medium:
    authorized_data_classifications: [public]
    authorized_risk_levels: [low, medium, high, critical]
    supports_structured_output: false
    supports_tool_calling: true
    max_context_tokens: 1000
    typical_latency_ms: 1000
    input_cost_usd_per_million_tokens: "0.10"
    output_cost_usd_per_million_tokens: "0.40"
    available: true
    allowed_agents: []
  reasoning-strong:
    authorized_data_classifications: [public]
    authorized_risk_levels: [low, medium, high, critical]
    supports_structured_output: false
    supports_tool_calling: true
    max_context_tokens: 1000
    typical_latency_ms: 1000
    input_cost_usd_per_million_tokens: "0.10"
    output_cost_usd_per_million_tokens: "0.40"
    available: true
    allowed_agents: []
  fast-structured-output:
    authorized_data_classifications: [public]
    authorized_risk_levels: [low, medium, high, critical]
    supports_structured_output: true
    supports_tool_calling: false
    max_context_tokens: 1000
    typical_latency_ms: 1000
    input_cost_usd_per_million_tokens: "0.10"
    output_cost_usd_per_million_tokens: "0.40"
    available: true
    allowed_agents: []
workloads:
  document_extraction: {model_group: fast-small, requires_tool_calling: false}
  cashflow_analysis: {model_group: reasoning-medium, requires_tool_calling: false}
  findings_correlation: {model_group: reasoning-strong, requires_tool_calling: false}
  opinion_drafting: {model_group: reasoning-strong, requires_tool_calling: false}
  json_repair: {model_group: fast-structured-output, requires_tool_calling: false}
"""


def test_load_routing_policy_reads_the_shipped_config() -> None:
    policy = load_routing_policy(_SHIPPED_POLICY_PATH)

    assert policy.schema_version == "1.0"
    assert policy.policy_id
    assert policy.policy_version
    assert policy.policy_digest.startswith("sha256:")
    assert set(policy.model_groups) == set(ModelGroup)
    assert set(policy.workloads) == set(Workload)
    assert policy.workloads[Workload.DOCUMENT_EXTRACTION].model_group == ModelGroup.FAST_SMALL
    assert policy.workloads[Workload.JSON_REPAIR].model_group == ModelGroup.FAST_STRUCTURED_OUTPUT


def test_load_routing_policy_computes_a_digest_that_changes_with_the_file_content(
    tmp_path: Path,
) -> None:
    policy_path = tmp_path / "routing_policy.yaml"
    policy_path.write_text(_VALID_YAML, encoding="utf-8")
    first_digest = load_routing_policy(policy_path).policy_digest

    policy_path.write_text(
        _VALID_YAML.replace('policy_version: "1.0.0"', 'policy_version: "2.0.0"')
    )
    second_digest = load_routing_policy(policy_path).policy_digest

    assert first_digest != second_digest


def test_load_routing_policy_fails_closed_when_policy_id_is_missing(tmp_path: Path) -> None:
    incomplete = _VALID_YAML.replace('policy_id: "test-policy"\n', "")
    policy_path = tmp_path / "routing_policy.yaml"
    policy_path.write_text(incomplete, encoding="utf-8")

    with pytest.raises(RoutingPolicyLoadError):
        load_routing_policy(policy_path)


def test_load_routing_policy_fails_closed_on_an_unknown_schema_version(tmp_path: Path) -> None:
    unknown_version = _VALID_YAML.replace('schema_version: "1.0"', 'schema_version: "999.0"')
    policy_path = tmp_path / "routing_policy.yaml"
    policy_path.write_text(unknown_version, encoding="utf-8")

    with pytest.raises(RoutingPolicyLoadError):
        load_routing_policy(policy_path)


def test_load_routing_policy_parses_a_minimal_valid_file(tmp_path: Path) -> None:
    policy_path = tmp_path / "routing_policy.yaml"
    policy_path.write_text(_VALID_YAML, encoding="utf-8")

    policy = load_routing_policy(policy_path)

    profile = policy.model_groups[ModelGroup.FAST_SMALL]
    assert profile.authorized_data_classifications == frozenset({DataClassification.PUBLIC})
    assert profile.allowed_agents == frozenset()


def test_load_routing_policy_fails_closed_on_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RoutingPolicyLoadError):
        load_routing_policy(tmp_path / "does-not-exist.yaml")


def test_load_routing_policy_fails_closed_on_malformed_yaml(tmp_path: Path) -> None:
    policy_path = tmp_path / "routing_policy.yaml"
    policy_path.write_text("not: valid: yaml: [", encoding="utf-8")

    with pytest.raises(RoutingPolicyLoadError):
        load_routing_policy(policy_path)


def test_load_routing_policy_fails_closed_on_unknown_field(tmp_path: Path) -> None:
    policy_path = tmp_path / "routing_policy.yaml"
    policy_path.write_text(_VALID_YAML + "\nunexpected_field: 1\n", encoding="utf-8")

    with pytest.raises(RoutingPolicyLoadError):
        load_routing_policy(policy_path)


def test_load_routing_policy_fails_closed_when_a_model_group_is_missing(tmp_path: Path) -> None:
    incomplete = _VALID_YAML.replace(
        """  fast-structured-output:
    authorized_data_classifications: [public]
    authorized_risk_levels: [low, medium, high, critical]
    supports_structured_output: true
    supports_tool_calling: false
    max_context_tokens: 1000
    typical_latency_ms: 1000
    input_cost_usd_per_million_tokens: "0.10"
    output_cost_usd_per_million_tokens: "0.40"
    available: true
    allowed_agents: []
""",
        "",
    )
    policy_path = tmp_path / "routing_policy.yaml"
    policy_path.write_text(incomplete, encoding="utf-8")

    with pytest.raises(RoutingPolicyLoadError):
        load_routing_policy(policy_path)


def test_load_routing_policy_fails_closed_when_a_workload_is_missing(tmp_path: Path) -> None:
    incomplete = _VALID_YAML.replace(
        "  json_repair: {model_group: fast-structured-output, requires_tool_calling: false}\n", ""
    )
    policy_path = tmp_path / "routing_policy.yaml"
    policy_path.write_text(incomplete, encoding="utf-8")

    with pytest.raises(RoutingPolicyLoadError):
        load_routing_policy(policy_path)


def test_load_routing_policy_fails_closed_on_a_duplicate_top_level_key(tmp_path: Path) -> None:
    """A second ``policy_version:`` must not silently overwrite the first (stock PyYAML default)."""
    duplicated = _VALID_YAML + '\npolicy_version: "9.9.9"\n'
    policy_path = tmp_path / "routing_policy.yaml"
    policy_path.write_text(duplicated, encoding="utf-8")

    with pytest.raises(RoutingPolicyLoadError):
        load_routing_policy(policy_path)
