"""Default :class:`~policy_model_router.application.ports.AvailabilityProvider` implementation.

Per ADR-0006, this adapter makes no network call: it simply returns the policy-declared
``available`` flag unchanged. It exists so the application layer already depends on the port
rather than on the static flag directly, ready for a future adapter backed by live
provider/gateway health without touching the use case or the domain constraints.
"""

from policy_model_router.domain.enums import ModelGroup


class StaticAvailabilityProvider:
    """Availability provider that trusts the routing policy's declared flag as-is."""

    async def is_available(self, _model_group: ModelGroup, declared_available: bool) -> bool:
        """Return ``declared_available`` unchanged; no live health check is performed."""
        return declared_available
