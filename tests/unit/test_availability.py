"""Unit tests for the default (static, no-network) availability provider."""

import pytest

from policy_model_router.adapters.availability import StaticAvailabilityProvider
from policy_model_router.domain.enums import ModelGroup


@pytest.mark.anyio
async def test_static_provider_passes_through_a_declared_available_group() -> None:
    provider = StaticAvailabilityProvider()

    assert await provider.is_available(ModelGroup.REASONING_STRONG, True) is True


@pytest.mark.anyio
async def test_static_provider_passes_through_a_declared_unavailable_group() -> None:
    provider = StaticAvailabilityProvider()

    assert await provider.is_available(ModelGroup.REASONING_STRONG, False) is False
