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

from dataclasses import replace

from policy_model_router.application.ports import AvailabilityProvider, Clock, IdGenerator
from policy_model_router.domain.catalog import RoutingPolicy
from policy_model_router.domain.constraints import CONSTRAINTS, ConstraintFailure
from policy_model_router.domain.enums import ModelGroup, ReasonCode
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

    def __init__(
        self,
        policy: RoutingPolicy,
        *,
        clock: Clock,
        id_generator: IdGenerator,
        availability: AvailabilityProvider,
        service_version: str,
        environment: str,
    ) -> None:
        """Bind the routing policy, its ports, and the deployment identity attached to decisions.

        Args:
            policy: The declarative routing policy loaded for this environment.
            clock: Source of the timestamp attached to each decision.
            id_generator: Source of each decision's unique identifier.
            availability: Resolves each model group's effective availability at decision time; see
                ADR-0006.
            service_version: This service's own version, attached to every decision so it is
                traceable to the code that produced it.
            environment: The deployment environment (e.g. ``"production"``), attached to every
                decision for the same reason.
        """
        self._policy = policy
        self._clock = clock
        self._id_generator = id_generator
        self._availability = availability
        self._service_version = service_version
        self._environment = environment

    async def route(self, request: RouteRequest) -> RouteDecision:
        """Return the routing decision for one request, or raise if none can be reached."""
        try:
            workload_rule = self._policy.workloads[request.workload]
        except KeyError as exc:
            raise IncompleteRoutingPolicyError(
                f"routing policy has no mapping for workload {request.workload.value!r}"
            ) from exc

        rejection_reasons: dict[ModelGroup, ConstraintFailure] = {}
        for model_group, profile in self._policy.model_groups.items():
            effective_profile = replace(
                profile,
                available=await self._availability.is_available(model_group, profile.available),
            )
            for constraint in CONSTRAINTS:
                failure = constraint(request, effective_profile, workload_rule)
                if failure is not None:
                    rejection_reasons[model_group] = failure
                    break

        selected = workload_rule.model_group
        if selected in rejection_reasons:
            failure = rejection_reasons[selected]
            raise NoViableModelGroupError(request.workload, selected, failure.message, failure.code)

        def _to_rejected_candidate(model_group: ModelGroup) -> RejectedCandidate:
            failure = rejection_reasons.get(model_group)
            if failure is not None:
                return RejectedCandidate(
                    model_group=model_group,
                    reason=failure.message,
                    reason_code=failure.code,
                    observed_value=failure.observed_value,
                    required_value=failure.required_value,
                )
            return RejectedCandidate(
                model_group=model_group,
                reason=(
                    f"workload {request.workload.value!r} is mapped to "
                    f"{selected.value!r}, not this group"
                ),
                reason_code=ReasonCode.WORKLOAD_MAPPED_ELSEWHERE,
                observed_value=request.workload.value,
                required_value=selected.value,
            )

        rejected_candidates = tuple(
            _to_rejected_candidate(model_group)
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
            policy_id=self._policy.policy_id,
            policy_version=self._policy.policy_version,
            policy_digest=self._policy.policy_digest,
            service_version=self._service_version,
            environment=self._environment,
        )
