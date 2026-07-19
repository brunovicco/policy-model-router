"""Eliminatory constraints for the deterministic model router (ADR-0005).

Each constraint is a pure predicate over one candidate model group: it returns ``None`` when the
candidate satisfies the constraint, or a human-readable rejection reason when it doesn't.
:data:`CONSTRAINTS` lists them in the order ADR-0005 specifies; the application use case runs them
in that order and stops at the first failure for each candidate.
"""

from collections.abc import Callable

from policy_model_router.domain.catalog import ModelGroupProfile, WorkloadRule
from policy_model_router.domain.routing import RouteRequest

Constraint = Callable[[RouteRequest, ModelGroupProfile, WorkloadRule], str | None]


def check_data_classification(
    request: RouteRequest, profile: ModelGroupProfile, _rule: WorkloadRule
) -> str | None:
    """Reject a group whose authorized deployments aren't cleared for the request's data.

    This is the router's half of the hard rule in ``docs/architecture-blueprint.md`` section 2.3:
    never route to a group unless every deployment behind it is cleared for the requested
    classification.
    """
    if request.data_classification in profile.authorized_data_classifications:
        return None
    return f"not authorized for data classification {request.data_classification.value!r}"


def check_structured_output(
    request: RouteRequest, profile: ModelGroupProfile, _rule: WorkloadRule
) -> str | None:
    """Reject a group that cannot produce structured output when the request requires it."""
    if not request.structured_output_required or profile.supports_structured_output:
        return None
    return "structured output required but not supported by this group"


def check_tool_calling(
    _request: RouteRequest, profile: ModelGroupProfile, rule: WorkloadRule
) -> str | None:
    """Reject a group that cannot call tools when the workload requires tool calling."""
    if not rule.requires_tool_calling or profile.supports_tool_calling:
        return None
    return "workload requires tool calling but this group does not support it"


def check_context_window(
    request: RouteRequest, profile: ModelGroupProfile, _rule: WorkloadRule
) -> str | None:
    """Reject a group whose context window is smaller than the estimated request context."""
    if request.context_tokens_estimated <= profile.max_context_tokens:
        return None
    return (
        f"estimated context {request.context_tokens_estimated} tokens exceeds "
        f"group limit of {profile.max_context_tokens} tokens"
    )


def check_max_cost(
    request: RouteRequest, profile: ModelGroupProfile, _rule: WorkloadRule
) -> str | None:
    """Reject a group whose estimated cost exceeds the request's cost ceiling."""
    if profile.estimated_cost_usd <= request.max_cost_usd:
        return None
    return (
        f"estimated cost {profile.estimated_cost_usd} usd exceeds "
        f"request ceiling of {request.max_cost_usd} usd"
    )


def check_max_latency(
    request: RouteRequest, profile: ModelGroupProfile, _rule: WorkloadRule
) -> str | None:
    """Reject a group whose typical latency exceeds the request's latency ceiling."""
    if profile.typical_latency_ms <= request.max_latency_ms:
        return None
    return (
        f"typical latency {profile.typical_latency_ms}ms exceeds "
        f"request ceiling of {request.max_latency_ms}ms"
    )


def check_availability(
    _request: RouteRequest, profile: ModelGroupProfile, _rule: WorkloadRule
) -> str | None:
    """Reject a group that is currently marked unavailable in the routing policy."""
    if profile.available:
        return None
    return "model group is marked unavailable in the routing policy"


def check_agent_allowlist(
    request: RouteRequest, profile: ModelGroupProfile, _rule: WorkloadRule
) -> str | None:
    """Reject a group whose allowlist excludes the requesting agent.

    An empty allowlist means the group has no per-agent restriction.
    """
    if not profile.allowed_agents or request.agent_name in profile.allowed_agents:
        return None
    return f"agent {request.agent_name!r} is not in this group's allowlist"


CONSTRAINTS: tuple[Constraint, ...] = (
    check_data_classification,
    check_structured_output,
    check_tool_calling,
    check_context_window,
    check_max_cost,
    check_max_latency,
    check_availability,
    check_agent_allowlist,
)
