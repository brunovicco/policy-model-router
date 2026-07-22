"""Unit tests for the default (static, no-network) availability provider."""

from policy_model_router.adapters.availability import StaticAvailabilityProvider
from policy_model_router.domain.enums import ModelGroup


def test_static_provider_passes_through_a_declared_available_group() -> None:
    provider = StaticAvailabilityProvider()

    assert provider.is_available(ModelGroup.REASONING_STRONG, True) is True


def test_static_provider_passes_through_a_declared_unavailable_group() -> None:
    provider = StaticAvailabilityProvider()

    assert provider.is_available(ModelGroup.REASONING_STRONG, False) is False
