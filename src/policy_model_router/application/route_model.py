"""The model-routing use case: ADR-0005's deterministic policy algorithm.

Every model group declared by the active policy is evaluated against ordered hard constraints. The
workload's policy-mapped group is selected only if it survives. Workloads and logical groups are
policy-defined identifiers; no system-wide enum participates in this decision.
"""

from dataclasses import replace

from policy_model_router.application.ports import AvailabilityProvider, Clock, IdGenerator
from policy_model_router.domain.catalog import RoutingPolicy
from policy_model_router.domain.constraints import CONSTRAINTS, ConstraintFailure
from policy_model_router.domain.enums import ReasonCode
from policy_model_router.domain.identifiers import ModelGroupId
from policy_model_router.domain.routing import (
    NoViableModelGroupError,
    RejectedCandidate,
    RejectedDecision,
    RouteDecision,
    RouteRequest,
)


class IncompleteRoutingPolicyError(Exception):
    """Fail-closed denial when the active policy does not define the requested workload."""


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
        """Bind the routing policy, ports, and deployment identity attached to decisions."""
        self._policy = policy
        self._clock = clock
        self._id_generator = id_generator
        self._availability = availability
        self._service_version = service_version
        self._environment = environment

    async def route(self, request: RouteRequest) -> RouteDecision:
        """Return the routing decision for one request, or fail closed if it is not authorized."""
        try:
            workload_rule = self._policy.workloads[request.workload]
        except KeyError as exc:
            raise IncompleteRoutingPolicyError(
                f"routing policy has no mapping for workload {request.workload.value!r}"
            ) from exc

        rejection_reasons: dict[ModelGroupId, ConstraintFailure] = {}
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
            raise NoViableModelGroupError(
                RejectedDecision(
                    schema_version=request.schema_version,
                    routing_decision_id=self._id_generator.new_id(),
                    decided_at=self._clock.now(),
                    workflow_id=request.workflow_id,
                    task_id=request.task_id,
                    workload=request.workload,
                    rejected_model_group=selected,
                    reason=failure.message,
                    reason_code=failure.code,
                    observed_value=failure.observed_value,
                    required_value=failure.required_value,
                    policy_id=self._policy.policy_id,
                    policy_version=self._policy.policy_version,
                    policy_digest=self._policy.policy_digest,
                    service_version=self._service_version,
                    environment=self._environment,
                )
            )

        def _to_rejected_candidate(model_group: ModelGroupId) -> RejectedCandidate:
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
