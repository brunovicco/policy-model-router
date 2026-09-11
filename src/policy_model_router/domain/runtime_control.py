"""Framework-free state and errors for the Governance runtime-control projection.

``RuntimeControlState.ACTIVE`` means the kill switch is *engaged* and the request must be denied;
``INACTIVE`` means execution may proceed. The polarity is the Governance projection's, mirrored
here exactly rather than reinterpreted - see ``application/runtime_control.py`` for the rule that
consumes it.
"""

from dataclasses import dataclass
from enum import StrEnum


class RuntimeControlState(StrEnum):
    """Effective runtime execution state projected by Governance."""

    INACTIVE = "inactive"
    ACTIVE = "active"


class RuntimeControlStoreError(RuntimeError):
    """Raised when the runtime-control projection cannot be trusted."""


class RuntimeControlEnforcementError(RuntimeError):
    """Fail-closed runtime-control denial with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        """Store the denial ``code`` without exposing backend details."""
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RuntimeControlSnapshot:
    """Exact P1.6a projection consumed by the Router."""

    agent_id: str
    control_epoch: int
    state: RuntimeControlState
    revoked_through_agent_version: int
    transition_id: str | None
    schema_version: str = "1.0"


__all__ = [
    "RuntimeControlEnforcementError",
    "RuntimeControlSnapshot",
    "RuntimeControlState",
    "RuntimeControlStoreError",
]
