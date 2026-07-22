"""Shared fixtures for integration tests (tests that need real infrastructure)."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Run ``@pytest.mark.anyio`` async tests on asyncio only."""
    return "asyncio"
