"""Eliminatory constraints for the deterministic model router (ADR-0005).

Each constraint is a pure predicate over one candidate model group: it returns ``None`` when the
candidate satisfies the constraint, or a :class:`ConstraintFailure` when it doesn't.
:data:`CONSTRAINTS` lists them in the order ADR-0005 specifies; the application use case runs them
in that order and stops at the first failure for each candidate.
"""

from collections.abc import Callable
from dataclasses import dataclass

from policy_model_router.domain.catalog import ModelGroupProfile, WorkloadRule
from policy_model_router.domain.enums import ReasonCode
from policy_model_router.domain.routing import RouteRequest


@dataclass(frozen=True, slots=True)
class ConstraintFailure:
    """A structured, machine-readable record of one failed constraint.

    ``message`` is the existing human-readable rejection reason; ``reason_code`` identifies which
    constraint failed without parsing that text; ``observed_value``/``required_value`` are the
    specific numbers or labels an auditor needs to verify the rejection without re-deriving them
    from the message.
    """

    code: ReasonCode
    message: str
    observed_value: str
    required_value: str


Constraint = Callable[[RouteRequest, ModelGroupProfile, WorkloadRule], ConstraintFailure | None]


def check_data_classification(
    request: RouteRequest, profile: ModelGroupProfile, _rule: WorkloadRule
) -> ConstraintFailure | None:
    """Reject a group whose authorized deployments aren't cleared for the request's data.

    This is the router's half of the hard rule in ``docs/architecture-blueprint.md`` section 2.3:
    never route to a group unless every deployment behind it is cleared for the requested
    classification.
    """
    if request.data_classification in profile.authorized_data_classifications:
        return None
    authorized = ", ".join(sorted(c.value for c in profile.authorized_data_classifications))
    return ConstraintFailure(
        code=ReasonCode.DATA_CLASSIFICATION_NOT_AUTHORIZED,
        message=f"not authorized for data classification {request.data_classification.value!r}",
        observed_value=request.data_classification.value,
        required_value=authorized,
    )


def check_risk_level(
    request: RouteRequest, profile: ModelGroupProfile, _rule: WorkloadRule
) -> ConstraintFailure | None:
    """Reject a group not authorized for the request's workflow risk tier.

    Independent of data classification: a group can be fully cleared for the data involved and
    still be unauthorized for a high-stakes decision, per ADR-0005's amendment.
    """
    if request.risk_level in profile.authorized_risk_levels:
        return None
    authorized = ", ".join(sorted(r.value for r in profile.authorized_risk_levels))
    return ConstraintFailure(
        code=ReasonCode.RISK_LEVEL_NOT_AUTHORIZED,
        message=f"not authorized for risk level {request.risk_level.value!r}",
        observed_value=request.risk_level.value,
        required_value=authorized,
    )


def check_structured_output(
    request: RouteRequest, profile: ModelGroupProfile, _rule: WorkloadRule
) -> ConstraintFailure | None:
    """Reject a group that cannot produce structured output when the request requires it."""
    if not request.structured_output_required or profile.supports_structured_output:
        return None
    return ConstraintFailure(
        code=ReasonCode.STRUCTURED_OUTPUT_UNSUPPORTED,
        message="structured output required but not supported by this group",
        observed_value="not supported",
        required_value="supported",
    )


def check_tool_calling(
    _request: RouteRequest, profile: ModelGroupProfile, rule: WorkloadRule
) -> ConstraintFailure | None:
    """Reject a group that cannot call tools when the workload requires tool calling."""
    if not rule.requires_tool_calling or profile.supports_tool_calling:
        return None
    return ConstraintFailure(
        code=ReasonCode.TOOL_CALLING_UNSUPPORTED,
        message="workload requires tool calling but this group does not support it",
        observed_value="not supported",
        required_value="supported",
    )


def check_context_window(
    request: RouteRequest, profile: ModelGroupProfile, _rule: WorkloadRule
) -> ConstraintFailure | None:
    """Reject a group whose context window is smaller than the estimated request context."""
    if request.context_tokens_estimated <= profile.max_context_tokens:
        return None
    return ConstraintFailure(
        code=ReasonCode.CONTEXT_WINDOW_EXCEEDED,
        message=(
            f"estimated context {request.context_tokens_estimated} tokens exceeds "
            f"group limit of {profile.max_context_tokens} tokens"
        ),
        observed_value=str(request.context_tokens_estimated),
        required_value=f"<= {profile.max_context_tokens}",
    )


def check_max_cost(
    request: RouteRequest, profile: ModelGroupProfile, _rule: WorkloadRule
) -> ConstraintFailure | None:
    """Reject a group whose token-based estimated cost exceeds the request's cost ceiling.

    Estimated cost is ``context_tokens_estimated`` (input) and ``max_output_tokens_estimated``
    (output) priced at the group's per-million-token rates - not a single flat number per group.
    """
    estimated_cost = profile.estimated_cost(
        input_tokens=request.context_tokens_estimated,
        output_tokens=request.max_output_tokens_estimated,
    )
    if estimated_cost <= request.max_cost_usd:
        return None
    return ConstraintFailure(
        code=ReasonCode.COST_CEILING_EXCEEDED,
        message=(
            f"estimated cost {estimated_cost} usd exceeds "
            f"request ceiling of {request.max_cost_usd} usd"
        ),
        observed_value=f"{estimated_cost} usd",
        required_value=f"<= {request.max_cost_usd} usd",
    )


def check_max_latency(
    request: RouteRequest, profile: ModelGroupProfile, _rule: WorkloadRule
) -> ConstraintFailure | None:
    """Reject a group whose typical latency exceeds the request's latency ceiling."""
    if profile.typical_latency_ms <= request.max_latency_ms:
        return None
    return ConstraintFailure(
        code=ReasonCode.LATENCY_CEILING_EXCEEDED,
        message=(
            f"typical latency {profile.typical_latency_ms}ms exceeds "
            f"request ceiling of {request.max_latency_ms}ms"
        ),
        observed_value=f"{profile.typical_latency_ms}ms",
        required_value=f"<= {request.max_latency_ms}ms",
    )


def check_availability(
    _request: RouteRequest, profile: ModelGroupProfile, _rule: WorkloadRule
) -> ConstraintFailure | None:
    """Reject a group that is currently marked unavailable in the routing policy."""
    if profile.available:
        return None
    return ConstraintFailure(
        code=ReasonCode.MODEL_GROUP_UNAVAILABLE,
        message="model group is marked unavailable in the routing policy",
        observed_value="unavailable",
        required_value="available",
    )


def check_agent_allowlist(
    request: RouteRequest, profile: ModelGroupProfile, _rule: WorkloadRule
) -> ConstraintFailure | None:
    """Reject a group whose allowlist excludes the requesting agent.

    An empty allowlist means the group has no per-agent restriction.

    ``required_value`` deliberately never lists ``profile.allowed_agents``: this candidate's
    rejection reason reaches every authenticated caller via ``rejected_candidates`` on an
    otherwise-successful ``/route`` response (not just the requesting agent), and
    ``entrypoints/http.py::_authenticate`` already treats configured agent identities as
    something the response must never reveal - this constraint must not undo that by a different
    path.
    """
    if not profile.allowed_agents or request.agent_name in profile.allowed_agents:
        return None
    return ConstraintFailure(
        code=ReasonCode.AGENT_NOT_ALLOWED,
        message=f"agent {request.agent_name!r} is not in this group's allowlist",
        observed_value=request.agent_name,
        required_value="an agent in this group's allowlist",
    )


CONSTRAINTS: tuple[Constraint, ...] = (
    check_data_classification,
    check_risk_level,
    check_structured_output,
    check_tool_calling,
    check_context_window,
    check_max_cost,
    check_max_latency,
    check_availability,
    check_agent_allowlist,
)
