"""Application-owned ports implemented by adapters.

Kept as narrow Protocols so tests can inject deterministic fakes instead of real time/identifier
sources, per ``.claude/rules/testing.md`` ("Unit tests isolate ... clock, randomness,
identifiers").
"""

from datetime import datetime
from typing import Protocol


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
