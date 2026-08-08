"""Tests for P1.6b runtime kill-switch and authorization revocation enforcement."""

import asyncio
import json
from datetime import datetime, timedelta
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from policy_model_router.runtime_authorization import (
    RuntimeAuthorizationError,
    RuntimeAuthorizationKeyStatus,
    RuntimeAuthorizationVerifier,
    TrustedRuntimeAuthorizationKey,
    TrustedRuntimeAuthorizationKeySet,
)
from policy_model_router.runtime_control import (
    InMemoryRuntimeControlStore,
    RedisRuntimeControlStore,
    RuntimeControlEnforcementError,
    RuntimeControlEnforcer,
    RuntimeControlSnapshot,
    RuntimeControlState,
    RuntimeControlStoreError,
)
from policy_model_router.runtime_violation import violation_category
from policy_model_router.runtime_violation_contract import RuntimeViolationCategory
from tests.unit.test_runtime_authorization import AGENT_ID, NOW, _route_request, _signed


class _CountingReplayGuard:
    def __init__(self) -> None:
        self.consume_count = 0

    async def consume(
        self,
        _authorization_id: UUID,
        *,
        expires_at: datetime,
        now: datetime,
    ) -> bool:
        del expires_at, now
        self.consume_count += 1
        return True

    async def ping(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _FakeRedis:
    def __init__(self, payload: object) -> None:
        self.payload = payload
        self.keys: list[str] = []
        self.pinged = False
        self.closed = False

    async def get(self, key: str) -> object:
        self.keys.append(key)
        return self.payload

    async def ping(self) -> bool:
        self.pinged = True
        return True

    async def aclose(self) -> None:
        self.closed = True


def _snapshot(
    *,
    state: RuntimeControlState = RuntimeControlState.INACTIVE,
    floor: int = 0,
    epoch: int = 1,
) -> RuntimeControlSnapshot:
    return RuntimeControlSnapshot(
        agent_id=str(AGENT_ID),
        control_epoch=epoch,
        state=state,
        revoked_through_agent_version=floor,
        transition_id="77777777-7777-4777-8777-777777777777",
    )


def _verifier(
    private_key: Ed25519PrivateKey,
    *,
    runtime_control: RuntimeControlEnforcer,
    replay_guard: _CountingReplayGuard,
) -> RuntimeAuthorizationVerifier:
    key = TrustedRuntimeAuthorizationKey(
        kid="gov-ed25519-2026-08",
        status=RuntimeAuthorizationKeyStatus.ACTIVE,
        public_key=private_key.public_key(),
        not_before=NOW - timedelta(days=1),
        verify_until=NOW + timedelta(days=1),
    )
    return RuntimeAuthorizationVerifier(
        key_set=TrustedRuntimeAuthorizationKeySet(1, (key,)),
        replay_guard=replay_guard,
        issuer="verifiable-ai-governance:production",
        audience="policy-model-router",
        agent_bindings={"Agente de Parecer de Crédito PJ": AGENT_ID},
        expected_policy_id="baseline-governance-policy",
        expected_policy_version="1.0.0",
        expected_policy_digest="d" * 64,
        expected_control_catalog_id="verifiable-ai-governance-baseline",
        expected_control_catalog_version="1.0.0",
        expected_control_catalog_digest="e" * 64,
        runtime_control=runtime_control,
    )


def test_active_kill_switch_denies_before_replay_consumption() -> None:
    private_key = Ed25519PrivateKey.generate()
    replay_guard = _CountingReplayGuard()
    enforcer = RuntimeControlEnforcer(
        InMemoryRuntimeControlStore((_snapshot(state=RuntimeControlState.ACTIVE, floor=7),))
    )
    verifier = _verifier(private_key, runtime_control=enforcer, replay_guard=replay_guard)

    with pytest.raises(RuntimeAuthorizationError) as exc_info:
        asyncio.run(
            verifier.verify(
                _signed(private_key),
                _route_request(),
                now=NOW + timedelta(seconds=1),
            )
        )

    assert exc_info.value.code == "kill_switch_engaged"
    assert exc_info.value.authorization_verified is True
    assert replay_guard.consume_count == 0


def test_inactive_snapshot_revokes_pre_restore_authorization_before_replay() -> None:
    private_key = Ed25519PrivateKey.generate()
    replay_guard = _CountingReplayGuard()
    enforcer = RuntimeControlEnforcer(InMemoryRuntimeControlStore((_snapshot(floor=7, epoch=2),)))
    verifier = _verifier(private_key, runtime_control=enforcer, replay_guard=replay_guard)

    with pytest.raises(RuntimeAuthorizationError) as exc_info:
        asyncio.run(
            verifier.verify(
                _signed(private_key),
                _route_request(),
                now=NOW + timedelta(seconds=1),
            )
        )

    assert exc_info.value.code == "runtime_authorization_revoked"
    assert exc_info.value.authorization_verified is True
    assert replay_guard.consume_count == 0


def test_newer_agent_version_passes_runtime_control() -> None:
    enforcer = RuntimeControlEnforcer(InMemoryRuntimeControlStore((_snapshot(floor=6),)))

    snapshot = asyncio.run(enforcer.enforce(agent_id=AGENT_ID, agent_version=7))

    assert snapshot.revoked_through_agent_version == 6


def test_missing_snapshot_fails_closed() -> None:
    enforcer = RuntimeControlEnforcer(InMemoryRuntimeControlStore())

    with pytest.raises(RuntimeControlEnforcementError) as exc_info:
        asyncio.run(enforcer.enforce(agent_id=AGENT_ID, agent_version=7))

    assert exc_info.value.code == "runtime_control_unavailable"


def test_redis_snapshot_matches_governance_p16a_contract() -> None:
    payload = json.dumps(
        {
            "agent_id": str(AGENT_ID),
            "control_epoch": 42,
            "revoked_through_agent_version": 17,
            "schema_version": "1.0",
            "state": "inactive",
            "transition_id": "77777777-7777-4777-8777-777777777777",
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    client = _FakeRedis(payload)
    store = RedisRuntimeControlStore(
        client,
        key_prefix="verifiable-ai-governance:runtime-control:v1:agent:",
    )

    snapshot = asyncio.run(store.read(str(AGENT_ID)))

    assert snapshot == RuntimeControlSnapshot(
        agent_id=str(AGENT_ID),
        control_epoch=42,
        state=RuntimeControlState.INACTIVE,
        revoked_through_agent_version=17,
        transition_id="77777777-7777-4777-8777-777777777777",
    )
    assert client.keys == ["verifiable-ai-governance:runtime-control:v1:agent:" + str(AGENT_ID)]


def test_redis_snapshot_rejects_agent_mismatch_and_unknown_fields() -> None:
    wrong_agent = UUID("99999999-9999-4999-8999-999999999999")
    payload = json.dumps(
        {
            "agent_id": str(wrong_agent),
            "control_epoch": 1,
            "revoked_through_agent_version": 0,
            "schema_version": "1.0",
            "state": "inactive",
            "transition_id": None,
            "unexpected": True,
        }
    )
    store = RedisRuntimeControlStore(
        _FakeRedis(payload),
        key_prefix="verifiable-ai-governance:runtime-control:v1:agent:",
    )

    with pytest.raises(RuntimeControlStoreError):
        asyncio.run(store.read(str(AGENT_ID)))


@pytest.mark.parametrize(
    "code",
    ["kill_switch_engaged", "runtime_authorization_revoked", "runtime_control_unavailable"],
)
def test_p16b_reason_codes_remain_p14_authorization_violations(code: str) -> None:
    assert violation_category(code) is RuntimeViolationCategory.AUTHORIZATION


class _ExplodingRedis:
    async def get(self, _key: str) -> object:
        raise OSError("redis unavailable")

    async def ping(self) -> bool:
        raise OSError("redis unavailable")

    async def aclose(self) -> None:
        return None


class _RaisingStore:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.closed = False

    async def read(self, _agent_id: str) -> RuntimeControlSnapshot | None:
        raise self.exc

    async def ping(self) -> None:
        raise self.exc

    async def close(self) -> None:
        self.closed = True


def _raw_snapshot(**updates: object) -> str:
    document: dict[str, object] = {
        "agent_id": str(AGENT_ID),
        "control_epoch": 1,
        "revoked_through_agent_version": 0,
        "schema_version": "1.0",
        "state": "inactive",
        "transition_id": None,
    }
    document.update(updates)
    return json.dumps(document)


def test_in_memory_store_lifecycle_is_available() -> None:
    store = InMemoryRuntimeControlStore((_snapshot(),))

    asyncio.run(store.ping())
    asyncio.run(store.close())


def test_redis_store_rejects_invalid_constructor_arguments() -> None:
    with pytest.raises(ValueError):
        RedisRuntimeControlStore(_FakeRedis(None), key_prefix="   ")

    with pytest.raises(ValueError):
        RedisRuntimeControlStore(
            _FakeRedis(None),
            key_prefix="runtime:",
            max_snapshot_bytes=511,
        )


def test_redis_store_handles_missing_and_bytes_snapshots() -> None:
    missing = RedisRuntimeControlStore(_FakeRedis(None), key_prefix="runtime:")
    assert asyncio.run(missing.read(str(AGENT_ID))) is None

    store = RedisRuntimeControlStore(
        _FakeRedis(_raw_snapshot().encode("utf-8")),
        key_prefix="runtime:",
    )
    snapshot = asyncio.run(store.read(str(AGENT_ID)))
    assert snapshot is not None
    assert snapshot.agent_id == str(AGENT_ID)


def test_redis_store_rejects_untrusted_representations() -> None:
    cases: tuple[object, ...] = (
        b"\xff",
        123,
        "x" * 513,
        "{not-json",
    )

    for payload in cases:
        store = RedisRuntimeControlStore(
            _FakeRedis(payload),
            key_prefix="runtime:",
            max_snapshot_bytes=512,
        )
        with pytest.raises(RuntimeControlStoreError):
            asyncio.run(store.read(str(AGENT_ID)))

    store = RedisRuntimeControlStore(_FakeRedis(None), key_prefix="runtime:")
    with pytest.raises(RuntimeControlStoreError):
        asyncio.run(store.read(""))


def test_redis_store_rejects_invalid_snapshot_fields() -> None:
    wrong_agent = UUID("99999999-9999-4999-8999-999999999999")

    invalid_documents = (
        _raw_snapshot(agent_id=str(wrong_agent)),
        _raw_snapshot(schema_version="2.0"),
        _raw_snapshot(control_epoch=-1),
        _raw_snapshot(transition_id=""),
        _raw_snapshot(state="paused"),
    )

    for payload in invalid_documents:
        store = RedisRuntimeControlStore(
            _FakeRedis(payload),
            key_prefix="runtime:",
        )
        with pytest.raises(RuntimeControlStoreError):
            asyncio.run(store.read(str(AGENT_ID)))


def test_redis_store_wraps_backend_failure_and_closes() -> None:
    failing = RedisRuntimeControlStore(
        _ExplodingRedis(),
        key_prefix="runtime:",
    )

    with pytest.raises(RuntimeControlStoreError):
        asyncio.run(failing.read(str(AGENT_ID)))

    with pytest.raises(RuntimeControlStoreError):
        asyncio.run(failing.ping())

    client = _FakeRedis(None)
    store = RedisRuntimeControlStore(client, key_prefix="runtime:")

    asyncio.run(store.ping())
    asyncio.run(store.close())

    assert client.pinged is True
    assert client.closed is True


@pytest.mark.parametrize(
    "error",
    [
        RuntimeControlStoreError("bad projection"),
        OSError("backend failure"),
    ],
)
def test_enforcer_wraps_projection_read_failures(error: Exception) -> None:
    enforcer = RuntimeControlEnforcer(_RaisingStore(error))

    with pytest.raises(RuntimeControlEnforcementError) as exc_info:
        asyncio.run(
            enforcer.enforce(
                agent_id=AGENT_ID,
                agent_version=7,
            )
        )

    assert exc_info.value.code == "runtime_control_unavailable"


@pytest.mark.parametrize(
    "error",
    [
        RuntimeControlStoreError("bad projection"),
        OSError("backend failure"),
    ],
)
def test_enforcer_wraps_projection_ping_failures(error: Exception) -> None:
    store = _RaisingStore(error)
    enforcer = RuntimeControlEnforcer(store)

    with pytest.raises(RuntimeControlEnforcementError) as exc_info:
        asyncio.run(enforcer.ping())

    assert exc_info.value.code == "runtime_control_unavailable"

    asyncio.run(enforcer.close())
    assert store.closed is True
