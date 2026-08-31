"""Default static AvailabilityProvider implementation."""

from policy_model_router.domain.identifiers import ModelGroupId


class StaticAvailabilityProvider:
    """Availability provider that trusts the routing policy's declared flag as-is."""

    async def is_available(self, _model_group: ModelGroupId, declared_available: bool) -> bool:
        """Return ``declared_available`` unchanged; no live health check is performed."""
        return declared_available
