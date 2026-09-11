"""Unit tests for the default (static, no-network) availability provider."""

import pytest

from policy_model_router.adapters.availability import StaticAvailabilityProvider
from policy_model_router.domain.enums import ModelGroup


@pytest.mark.anyio
async def test_static_provider_passes_declared_flags_through_unchanged() -> None:
    provider = StaticAvailabilityProvider()
    declared = {ModelGroup.REASONING_STRONG: True, ModelGroup.FAST_SMALL: False}

    assert await provider.resolve(declared) == declared


@pytest.mark.anyio
async def test_static_provider_resolves_every_requested_group() -> None:
    """A caller treats an omitted group as unavailable, so the static provider must omit none."""
    provider = StaticAvailabilityProvider()
    declared = {
        ModelGroup.FAST_SMALL: True,
        ModelGroup.REASONING_MEDIUM: True,
        ModelGroup.REASONING_STRONG: True,
        ModelGroup.FAST_STRUCTURED_OUTPUT: True,
    }

    assert (await provider.resolve(declared)).keys() == declared.keys()


@pytest.mark.anyio
async def test_static_provider_returns_an_empty_result_for_an_empty_policy() -> None:
    provider = StaticAvailabilityProvider()

    assert await provider.resolve({}) == {}
