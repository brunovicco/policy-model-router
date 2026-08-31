"""Application-owned ports implemented by adapters.

Kept as narrow Protocols so tests can inject deterministic fakes instead of real time/identifier
sources.
"""

from datetime import datetime
from typing import Protocol

from policy_model_router.domain.identifiers import ModelGroupId


class Clock(Protocol):
    """Port for reading the current time."""

    def now(self) -> datetime:
        """Return the current UTC, timezone-aware time."""
        ...


class IdGenerator(Protocol):
    """Port for generating unique identifiers."""

    def new_id(self) -> str:
        """Return a new, unique identifier."""
        ...


class AvailabilityProvider(Protocol):
    """Port for resolving a model group's effective availability at decision time."""

    async def is_available(
        self, model_group: ModelGroupId, declared_available: bool
    ) -> bool:
        """Return whether ``model_group`` is available given its policy-declared default."""
        ...
