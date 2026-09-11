"""Default static AvailabilityProvider implementation."""

from collections.abc import Mapping

from policy_model_router.domain.identifiers import ModelGroupId


class StaticAvailabilityProvider:
    """Availability provider that trusts the routing policy's declared flags as-is."""

    async def resolve(self, declared: Mapping[ModelGroupId, bool]) -> Mapping[ModelGroupId, bool]:
        """Return ``declared`` unchanged; no live health check is performed."""
        return declared
