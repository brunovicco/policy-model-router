"""Application-owned ports implemented by adapters.

Kept as narrow Protocols so tests can inject deterministic fakes instead of real time/identifier
sources, per ``.claude/rules/testing.md`` ("Unit tests isolate ... clock, randomness,
identifiers").
"""

from datetime import datetime
from typing import Protocol

from policy_model_router.domain.enums import ModelGroup


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
    """Port for resolving a model group's effective availability at decision time.

    Async (ADR-0006's amendment) so a future live provider/gateway health check can await a
    network call without blocking the event loop or every other concurrent ``/route`` request.
    No implementation shipped today calls out over the network; :class:`StaticAvailabilityProvider`
    awaits nothing and returns immediately.
    """

    async def is_available(self, model_group: ModelGroup, declared_available: bool) -> bool:
        """Return whether ``model_group`` is available, given its policy-declared default.

        Args:
            model_group: The model group being evaluated.
            declared_available: The ``available`` flag from the loaded routing policy for this
                group, i.e. the static fallback signal.
        """
        ...
