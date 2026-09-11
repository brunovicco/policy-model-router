"""Application-owned ports implemented by adapters.

Kept as narrow Protocols so tests can inject deterministic fakes instead of real time/identifier
sources.
"""

from collections.abc import Mapping
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
    """Port for resolving effective model-group availability at decision time.

    Resolves the whole candidate set in one call rather than one group at a time. The only
    implementation shipped today does no I/O, but the adapter this port exists for (ADR-0006) polls
    a provider or gateway: per-group resolution would make that N sequential network round trips on
    the hot path of every decision, and the shape is far cheaper to fix now than after such an
    adapter exists.
    """

    async def resolve(self, declared: Mapping[ModelGroupId, bool]) -> Mapping[ModelGroupId, bool]:
        """Return effective availability for every group in ``declared``.

        Args:
            declared: Each candidate model group mapped to the flag the active policy declares
                for it.

        Returns:
            The same keys, mapped to effective availability. A group the implementation omits is
            treated as unavailable by the caller, so a partial or degraded answer can never make a
            group more available than the policy declared.
        """
        ...
