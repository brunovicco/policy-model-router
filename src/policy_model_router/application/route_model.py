"""The model-routing use case: ADR-0005's two-step deterministic algorithm.

1. Every model group in the policy's catalog is checked against the request's workload rule,
   running the eliminatory constraints from :mod:`policy_model_router.domain.constraints` in
   order; a candidate is rejected at the first constraint it fails.
2. The workload's mapped model group (from the declarative table) is selected if it survived
   step 1. Every other candidate becomes a rejected candidate with a reason - either the
   constraint that eliminated it, or, if it passed every constraint, the fact that the workload
   simply maps elsewhere. If the mapped group itself was eliminated, routing fails outright: the
   MVP has no weighted-score fallback (that is Phase 3).
"""

from policy_model_router.application.ports import Clock, IdGenerator
from policy_model_router.domain.catalog import RoutingPolicy
from policy_model_router.domain.constraints import CONSTRAINTS
from policy_model_router.domain.enums import ModelGroup
from policy_model_router.domain.routing import (
    NoViableModelGroupError,
    RejectedCandidate,
    RouteDecision,
    RouteRequest,
)


class IncompleteRoutingPolicyError(Exception):
    """Raised when the routing policy has no workload rule for a request's workload."""


class RouteModelUseCase:
    """Evaluate one model-routing request against a declarative routing policy."""

    def __init__(self, policy: RoutingPolicy, *, clock: Clock, id_generator: IdGenerator) -> None:
        """Bind the routing policy and the clock/id-generator ports used to build decisions."""
        self._policy = policy
        self._clock = clock
        self._id_generator = id_generator

    def route(self, request: RouteRequest) -> RouteDecision:
        """Return the routing decision for one request, or raise if none can be reached."""
        try:
            workload_rule = self._policy.workloads[request.workload]
        except KeyError as exc:
            raise IncompleteRoutingPolicyError(
                f"routing policy has no mapping for workload {request.workload.value!r}"
            ) from exc

        rejection_reasons: dict[ModelGroup, str] = {}
        for model_group, profile in self._policy.model_groups.items():
            for constraint in CONSTRAINTS:
                reason = constraint(request, profile, workload_rule)
                if reason is not None:
                    rejection_reasons[model_group] = reason
                    break

        selected = workload_rule.model_group
        if selected in rejection_reasons:
            raise NoViableModelGroupError(request.workload, selected, rejection_reasons[selected])

        rejected_candidates = tuple(
            RejectedCandidate(
                model_group=model_group,
                reason=rejection_reasons.get(
                    model_group,
                    f"workload {request.workload.value!r} is mapped to "
                    f"{selected.value!r}, not this group",
                ),
            )
            for model_group in sorted(self._policy.model_groups, key=lambda group: group.value)
            if model_group != selected
        )

        return RouteDecision(
            schema_version=request.schema_version,
            routing_decision_id=self._id_generator.new_id(),
            decided_at=self._clock.now(),
            workflow_id=request.workflow_id,
            task_id=request.task_id,
            selected_model_group=selected,
            reason=(
                f"workload {request.workload.value!r} maps to model group "
                f"{selected.value!r} and satisfies all constraints"
            ),
            rejected_candidates=rejected_candidates,
        )
