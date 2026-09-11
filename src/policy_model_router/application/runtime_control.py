"""Runtime-control enforcement: the Governance kill switch and revocation floor.

Reads a Governance-owned projection through :class:`RuntimeControlStore` and denies execution when
the kill switch is engaged, when the signed agent version is at or below the revocation floor, or
when the projection cannot be trusted. An absent snapshot is a denial, never a default allow.
"""

from typing import Protocol
from uuid import UUID

from policy_model_router.domain.runtime_control import (
    RuntimeControlEnforcementError,
    RuntimeControlSnapshot,
    RuntimeControlState,
    RuntimeControlStoreError,
)


class RuntimeControlStore(Protocol):
    """Read-only projection boundary owned by the Router."""

    async def read(self, agent_id: str) -> RuntimeControlSnapshot | None:
        """Read the current snapshot for one signed Governance agent ID."""
        ...

    async def ping(self) -> None:
        """Raise when the projection backend is unavailable."""
        ...

    async def close(self) -> None:
        """Release projection resources."""
        ...


class RuntimeControlEnforcer:
    """Apply kill-switch and revocation-floor semantics before replay consumption."""

    def __init__(self, store: RuntimeControlStore) -> None:
        """Bind the trusted read-only projection store."""
        self._store = store

    async def enforce(self, *, agent_id: UUID, agent_version: int) -> RuntimeControlSnapshot:
        """Allow only an inactive snapshot whose revocation floor is below the signed version."""
        try:
            snapshot = await self._store.read(str(agent_id))
        except RuntimeControlStoreError as exc:
            raise RuntimeControlEnforcementError(
                "runtime_control_unavailable",
                "Runtime control state is unavailable",
            ) from exc
        except Exception as exc:
            raise RuntimeControlEnforcementError(
                "runtime_control_unavailable",
                "Runtime control state is unavailable",
            ) from exc
        if snapshot is None:
            raise RuntimeControlEnforcementError(
                "runtime_control_unavailable",
                "Runtime control state is unavailable",
            )
        if snapshot.state is RuntimeControlState.ACTIVE:
            raise RuntimeControlEnforcementError(
                "kill_switch_engaged",
                "Governance runtime kill switch is engaged",
            )
        if agent_version <= snapshot.revoked_through_agent_version:
            raise RuntimeControlEnforcementError(
                "runtime_authorization_revoked",
                "Runtime authorization predates the current Governance revocation floor",
            )
        return snapshot

    async def ping(self) -> None:
        """Require the runtime-control backend to be reachable."""
        try:
            await self._store.ping()
        except RuntimeControlStoreError as exc:
            raise RuntimeControlEnforcementError(
                "runtime_control_unavailable",
                "Runtime control state is unavailable",
            ) from exc
        except Exception as exc:
            raise RuntimeControlEnforcementError(
                "runtime_control_unavailable",
                "Runtime control state is unavailable",
            ) from exc

    async def close(self) -> None:
        """Release runtime-control resources."""
        await self._store.close()


__all__ = ["RuntimeControlEnforcer", "RuntimeControlStore"]
