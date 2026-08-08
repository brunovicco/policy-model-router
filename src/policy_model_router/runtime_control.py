"""Read and enforce Governance runtime-control snapshots at the Router boundary."""

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID


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


class InMemoryRuntimeControlStore:
    """Read-only process-local store for focused unit tests and local development."""

    def __init__(self, snapshots: tuple[RuntimeControlSnapshot, ...] = ()) -> None:
        """Index explicitly supplied snapshots by agent ID."""
        self._snapshots = {snapshot.agent_id: snapshot for snapshot in snapshots}

    async def read(self, agent_id: str) -> RuntimeControlSnapshot | None:
        """Return the explicitly configured snapshot, if present."""
        snapshot = self._snapshots.get(agent_id)
        if snapshot is not None and snapshot.agent_id != agent_id:
            raise RuntimeControlStoreError("Runtime-control snapshot agent binding is invalid")
        return snapshot

    async def ping(self) -> None:
        """The process-local test store is always reachable."""
        return None

    async def close(self) -> None:
        """No resources are owned."""
        return None


class RedisRuntimeControlStore:
    """Strict read-only adapter for Governance P1.6a Redis projections."""

    def __init__(self, client: Any, *, key_prefix: str, max_snapshot_bytes: int = 4096) -> None:
        """Bind a Redis client to the exact Governance runtime-control namespace."""
        if not key_prefix.strip():
            raise ValueError("Runtime-control key prefix must not be empty")
        if not 512 <= max_snapshot_bytes <= 65_536:
            raise ValueError("Runtime-control snapshot byte limit is out of range")
        self._client = client
        self._key_prefix = key_prefix
        self._max_snapshot_bytes = max_snapshot_bytes

    async def read(self, agent_id: str) -> RuntimeControlSnapshot | None:
        """Read and strictly validate one bounded snapshot without fallback defaults."""
        if not agent_id:
            raise RuntimeControlStoreError("Runtime-control agent identifier is empty")
        try:
            raw = await self._client.get(f"{self._key_prefix}{agent_id}")
        except Exception as exc:
            raise RuntimeControlStoreError("Runtime-control Redis is unavailable") from exc
        if raw is None:
            return None
        if isinstance(raw, bytes):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise RuntimeControlStoreError("Runtime-control snapshot is not UTF-8") from exc
        if not isinstance(raw, str):
            raise RuntimeControlStoreError("Runtime-control snapshot has an invalid representation")
        if len(raw.encode("utf-8")) > self._max_snapshot_bytes:
            raise RuntimeControlStoreError("Runtime-control snapshot exceeds its size limit")
        return _parse_snapshot(raw, expected_agent_id=agent_id)

    async def ping(self) -> None:
        """Require the shared Redis backend to be reachable at startup."""
        try:
            await self._client.ping()
        except Exception as exc:
            raise RuntimeControlStoreError("Runtime-control Redis is unavailable") from exc

    async def close(self) -> None:
        """Close the owned Redis connection."""
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()


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


def _parse_snapshot(raw: str, *, expected_agent_id: str) -> RuntimeControlSnapshot:
    try:
        document = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeControlStoreError("Runtime-control snapshot is invalid JSON") from exc
    expected_fields = {
        "agent_id",
        "control_epoch",
        "revoked_through_agent_version",
        "schema_version",
        "state",
        "transition_id",
    }
    if not isinstance(document, dict) or set(document) != expected_fields:
        raise RuntimeControlStoreError("Runtime-control snapshot contains unsupported fields")

    agent_id = document["agent_id"]
    control_epoch = document["control_epoch"]
    revoked_version = document["revoked_through_agent_version"]
    transition_id = document["transition_id"]
    if agent_id != expected_agent_id:
        raise RuntimeControlStoreError("Runtime-control snapshot agent binding is invalid")
    if document["schema_version"] != "1.0":
        raise RuntimeControlStoreError("Runtime-control snapshot schema is unsupported")
    if (
        not isinstance(control_epoch, int)
        or isinstance(control_epoch, bool)
        or control_epoch < 0
        or not isinstance(revoked_version, int)
        or isinstance(revoked_version, bool)
        or revoked_version < 0
    ):
        raise RuntimeControlStoreError("Runtime-control snapshot counters are invalid")
    if transition_id is not None and (
        not isinstance(transition_id, str) or not 1 <= len(transition_id) <= 200
    ):
        raise RuntimeControlStoreError("Runtime-control transition identifier is invalid")
    try:
        state = RuntimeControlState(document["state"])
    except (TypeError, ValueError) as exc:
        raise RuntimeControlStoreError("Runtime-control state is invalid") from exc
    return RuntimeControlSnapshot(
        agent_id=agent_id,
        control_epoch=control_epoch,
        state=state,
        revoked_through_agent_version=revoked_version,
        transition_id=transition_id,
    )
