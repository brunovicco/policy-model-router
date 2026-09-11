"""Tests for the runtime-authorization adapters: trusted key material and replay consumption.

This is the code that decides which signing keys are trusted and whether an authorization has
already been spent, so every rejection path is asserted by its stable code rather than by the fact
that something was raised.
"""

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from policy_model_router.adapters.runtime_authorization import (
    InMemoryRuntimeAuthorizationReplayGuard,
    RedisRuntimeAuthorizationReplayGuard,
    load_trusted_key_set,
    parse_agent_bindings,
)
from policy_model_router.domain.runtime_authorization import (
    RuntimeAuthorizationError,
    RuntimeAuthorizationKeyStatus,
)

NOW = datetime(2026, 8, 7, 18, 0, tzinfo=UTC)


def _public_x(private_key: Ed25519PrivateKey) -> str:
    raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _entry(private_key: Ed25519PrivateKey, **overrides: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "kid": "governance-key-2026a",
        "status": "active",
        "not_before": "2026-01-01T00:00:00Z",
        "verify_until": "2027-01-01T00:00:00Z",
        "jwk": {"kty": "OKP", "crv": "Ed25519", "x": _public_x(private_key)},
    }
    entry.update(overrides)
    return entry


def _document(private_key: Ed25519PrivateKey, **overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": "1.0",
        "generation": 3,
        "keys": [_entry(private_key)],
    }
    document.update(overrides)
    return document


def _write(tmp_path: Path, document: object) -> Path:
    path = tmp_path / "keys.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@pytest.fixture
def private_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def test_load_trusted_key_set_returns_a_resolvable_key(
    tmp_path: Path, private_key: Ed25519PrivateKey
) -> None:
    key_set = load_trusted_key_set(_write(tmp_path, _document(private_key)))

    assert key_set.generation == 3
    resolved = key_set.resolve("governance-key-2026a")
    assert resolved is not None
    assert resolved.status is RuntimeAuthorizationKeyStatus.ACTIVE
    assert resolved.not_before < resolved.verify_until


def test_resolve_never_falls_back_to_another_key(
    tmp_path: Path, private_key: Ed25519PrivateKey
) -> None:
    """An unknown key id resolves to nothing, not to whichever key happens to be first."""
    key_set = load_trusted_key_set(_write(tmp_path, _document(private_key)))

    assert key_set.resolve("some-other-kid") is None


def test_load_trusted_key_set_reports_an_unreadable_path(tmp_path: Path) -> None:
    with pytest.raises(RuntimeAuthorizationError) as excinfo:
        load_trusted_key_set(tmp_path / "absent.json")

    assert excinfo.value.code == "key_set_unavailable"


def test_load_trusted_key_set_bounds_the_file_size(
    tmp_path: Path, private_key: Ed25519PrivateKey
) -> None:
    path = _write(tmp_path, _document(private_key))

    with pytest.raises(RuntimeAuthorizationError) as excinfo:
        load_trusted_key_set(path, max_bytes=16)

    assert excinfo.value.code == "key_set_too_large"


def test_load_trusted_key_set_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "keys.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(RuntimeAuthorizationError) as excinfo:
        load_trusted_key_set(path)

    assert excinfo.value.code == "invalid_key_set"


def test_load_trusted_key_set_rejects_non_utf8_bytes(tmp_path: Path) -> None:
    path = tmp_path / "keys.json"
    path.write_bytes(b"\xff\xfe\x00")

    with pytest.raises(RuntimeAuthorizationError) as excinfo:
        load_trusted_key_set(path)

    assert excinfo.value.code == "invalid_key_set"


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"schema_version": "2.0"}, id="unsupported-schema-version"),
        pytest.param({"generation": 0}, id="generation-below-one"),
        pytest.param({"generation": True}, id="generation-is-a-bool"),
        pytest.param({"generation": "3"}, id="generation-is-a-string"),
        pytest.param({"keys": []}, id="no-keys"),
        pytest.param({"keys": {}}, id="keys-not-a-list"),
        pytest.param({"unexpected": 1}, id="unsupported-top-level-field"),
    ],
)
def test_load_trusted_key_set_rejects_a_malformed_document(
    tmp_path: Path, private_key: Ed25519PrivateKey, overrides: dict[str, Any]
) -> None:
    path = _write(tmp_path, _document(private_key, **overrides))

    with pytest.raises(RuntimeAuthorizationError) as excinfo:
        load_trusted_key_set(path)

    assert excinfo.value.code == "invalid_key_set"


def test_load_trusted_key_set_rejects_a_document_that_is_not_an_object(tmp_path: Path) -> None:
    with pytest.raises(RuntimeAuthorizationError) as excinfo:
        load_trusted_key_set(_write(tmp_path, ["not", "an", "object"]))

    assert excinfo.value.code == "invalid_key_set"


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"kid": ""}, id="empty-kid"),
        pytest.param({"kid": 7}, id="non-string-kid"),
        pytest.param({"status": "retired"}, id="unknown-status"),
        pytest.param({"jwk": {"kty": "RSA", "crv": "Ed25519", "x": "AA"}}, id="non-okp-key-type"),
        pytest.param({"jwk": {"kty": "OKP", "crv": "P-256", "x": "AA"}}, id="wrong-curve"),
        pytest.param({"jwk": {"kty": "OKP", "crv": "Ed25519"}}, id="jwk-missing-x"),
        pytest.param({"jwk": {"kty": "OKP", "crv": "Ed25519", "x": 5}}, id="non-string-x"),
        pytest.param({"jwk": {"kty": "OKP", "crv": "Ed25519", "x": "!!!"}}, id="x-not-base64url"),
        pytest.param({"jwk": {"kty": "OKP", "crv": "Ed25519", "x": "AAAA"}}, id="x-not-32-bytes"),
        pytest.param({"not_before": "2028-01-01T00:00:00Z"}, id="window-inverted"),
        pytest.param({"not_before": "2026-01-01T00:00:00+03:00"}, id="not-before-not-utc"),
        pytest.param({"verify_until": "whenever"}, id="unparseable-timestamp"),
        pytest.param({"extra": True}, id="unsupported-entry-field"),
    ],
)
def test_load_trusted_key_set_rejects_a_malformed_key_entry(
    tmp_path: Path, private_key: Ed25519PrivateKey, overrides: dict[str, Any]
) -> None:
    document = _document(private_key, keys=[_entry(private_key, **overrides)])

    with pytest.raises(RuntimeAuthorizationError) as excinfo:
        load_trusted_key_set(_write(tmp_path, document))

    assert excinfo.value.code == "invalid_key_set"


def test_load_trusted_key_set_rejects_an_entry_that_is_not_an_object(
    tmp_path: Path, private_key: Ed25519PrivateKey
) -> None:
    with pytest.raises(RuntimeAuthorizationError) as excinfo:
        load_trusted_key_set(_write(tmp_path, _document(private_key, keys=["nope"])))

    assert excinfo.value.code == "invalid_key_set"


def test_load_trusted_key_set_rejects_a_duplicate_key_identifier(
    tmp_path: Path, private_key: Ed25519PrivateKey
) -> None:
    """Two entries under one kid would make `resolve` order-dependent."""
    document = _document(private_key, keys=[_entry(private_key), _entry(private_key)])

    with pytest.raises(RuntimeAuthorizationError) as excinfo:
        load_trusted_key_set(_write(tmp_path, document))

    assert excinfo.value.code == "invalid_key_set"


def test_load_trusted_key_set_requires_at_least_one_active_key(
    tmp_path: Path, private_key: Ed25519PrivateKey
) -> None:
    """A set of only retiring or revoked keys can verify nothing, so it fails at load."""
    document = _document(
        private_key,
        keys=[
            _entry(private_key, kid="retiring", status="retiring"),
            _entry(private_key, kid="revoked", status="revoked"),
        ],
    )

    with pytest.raises(RuntimeAuthorizationError) as excinfo:
        load_trusted_key_set(_write(tmp_path, document))

    assert excinfo.value.code == "invalid_key_set"


def test_a_retiring_key_loads_alongside_an_active_one(
    tmp_path: Path, private_key: Ed25519PrivateKey
) -> None:
    """Rotation needs both present at once; only `revoked` is rejected at verification time."""
    document = _document(
        private_key,
        keys=[_entry(private_key), _entry(private_key, kid="previous", status="retiring")],
    )

    key_set = load_trusted_key_set(_write(tmp_path, document))

    previous = key_set.resolve("previous")
    assert previous is not None
    assert previous.status is RuntimeAuthorizationKeyStatus.RETIRING


def test_parse_agent_bindings_maps_names_to_uuids() -> None:
    agent_id = "0f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"

    bindings = parse_agent_bindings(json.dumps({"credit-analysis-agent": agent_id}))

    assert bindings == {"credit-analysis-agent": UUID(agent_id)}


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("{not json", id="invalid-json"),
        pytest.param("[]", id="not-an-object"),
        pytest.param("{}", id="empty-object"),
        pytest.param('{"": "0f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"}', id="empty-agent-name"),
        pytest.param('{"agent": 7}', id="non-string-agent-id"),
        pytest.param('{"agent": "not-a-uuid"}', id="agent-id-not-a-uuid"),
    ],
)
def test_parse_agent_bindings_rejects_malformed_input(raw: str) -> None:
    with pytest.raises(RuntimeAuthorizationError) as excinfo:
        parse_agent_bindings(raw)

    assert excinfo.value.code in {"invalid_agent_bindings", "invalid_key_set"}


def test_parse_agent_bindings_bounds_the_agent_name() -> None:
    raw = json.dumps({"a" * 201: "0f1a2b3c-4d5e-6f70-8192-a3b4c5d6e7f8"})

    with pytest.raises(RuntimeAuthorizationError) as excinfo:
        parse_agent_bindings(raw)

    assert excinfo.value.code == "invalid_agent_bindings"


def test_in_memory_replay_guard_consumes_an_identifier_exactly_once() -> None:
    guard = InMemoryRuntimeAuthorizationReplayGuard()
    authorization_id = UUID("11111111-2222-3333-4444-555555555555")
    expires_at = NOW + timedelta(minutes=5)

    first = asyncio.run(guard.consume(authorization_id, expires_at=expires_at, now=NOW))
    second = asyncio.run(guard.consume(authorization_id, expires_at=expires_at, now=NOW))

    assert first is True
    assert second is False


def test_in_memory_replay_guard_forgets_an_expired_identifier() -> None:
    """Entries are dropped once they expire, so the guard does not grow without bound."""
    guard = InMemoryRuntimeAuthorizationReplayGuard()
    authorization_id = UUID("11111111-2222-3333-4444-555555555555")
    expires_at = NOW + timedelta(minutes=5)

    asyncio.run(guard.consume(authorization_id, expires_at=expires_at, now=NOW))
    later = asyncio.run(
        guard.consume(
            authorization_id,
            expires_at=expires_at + timedelta(minutes=10),
            now=NOW + timedelta(minutes=6),
        )
    )

    assert later is True


def test_in_memory_replay_guard_fails_closed_at_capacity() -> None:
    """Silently evicting a live identifier would turn the bound into a replay window."""
    guard = InMemoryRuntimeAuthorizationReplayGuard(max_entries=1)
    expires_at = NOW + timedelta(minutes=5)
    asyncio.run(
        guard.consume(UUID("11111111-2222-3333-4444-555555555555"), expires_at=expires_at, now=NOW)
    )

    with pytest.raises(RuntimeAuthorizationError) as excinfo:
        asyncio.run(
            guard.consume(
                UUID("66666666-7777-8888-9999-aaaaaaaaaaaa"), expires_at=expires_at, now=NOW
            )
        )

    assert excinfo.value.code == "replay_store_full"


def test_in_memory_replay_guard_requires_a_positive_bound() -> None:
    with pytest.raises(ValueError, match="max_entries"):
        InMemoryRuntimeAuthorizationReplayGuard(max_entries=0)


def test_in_memory_replay_guard_lifecycle_is_a_no_op() -> None:
    guard = InMemoryRuntimeAuthorizationReplayGuard()

    assert asyncio.run(guard.ping()) is None
    assert asyncio.run(guard.close()) is None


class _FakeRedis:
    def __init__(self, *, result: object = True, fails: bool = False) -> None:
        self.result = result
        self.fails = fails
        self.calls: list[tuple[str, int]] = []
        self.closed = False

    async def set(self, key: str, _value: str, *, nx: bool, ex: int) -> object:
        if self.fails:
            raise ConnectionError("redis is down")
        self.calls.append((key, ex))
        assert nx is True
        return self.result

    async def ping(self) -> None:
        if self.fails:
            raise ConnectionError("redis is down")

    async def aclose(self) -> None:
        self.closed = True


def test_redis_replay_guard_namespaces_the_key_and_sets_a_ttl() -> None:
    client = _FakeRedis()
    guard = RedisRuntimeAuthorizationReplayGuard(client, key_prefix="pmr:auth:")
    authorization_id = UUID("11111111-2222-3333-4444-555555555555")

    fresh = asyncio.run(
        guard.consume(authorization_id, expires_at=NOW + timedelta(minutes=5), now=NOW)
    )

    assert fresh is True
    assert client.calls == [(f"pmr:auth:{authorization_id}", 300)]


def test_redis_replay_guard_reports_an_already_consumed_identifier() -> None:
    guard = RedisRuntimeAuthorizationReplayGuard(_FakeRedis(result=None), key_prefix="pmr:")

    consumed = asyncio.run(
        guard.consume(
            UUID("11111111-2222-3333-4444-555555555555"),
            expires_at=NOW + timedelta(minutes=5),
            now=NOW,
        )
    )

    assert consumed is False


def test_redis_replay_guard_fails_closed_when_the_backend_is_unavailable() -> None:
    """Unlike the rate limiter, replay protection must never fail open."""
    guard = RedisRuntimeAuthorizationReplayGuard(_FakeRedis(fails=True), key_prefix="pmr:")

    with pytest.raises(RuntimeAuthorizationError) as excinfo:
        asyncio.run(
            guard.consume(
                UUID("11111111-2222-3333-4444-555555555555"),
                expires_at=NOW + timedelta(minutes=5),
                now=NOW,
            )
        )

    assert excinfo.value.code == "replay_store_unavailable"


def test_redis_replay_guard_requires_a_reachable_backend_at_startup() -> None:
    guard = RedisRuntimeAuthorizationReplayGuard(_FakeRedis(fails=True), key_prefix="pmr:")

    with pytest.raises(RuntimeAuthorizationError) as excinfo:
        asyncio.run(guard.ping())

    assert excinfo.value.code == "replay_store_unavailable"


def test_redis_replay_guard_closes_the_client_it_owns() -> None:
    client = _FakeRedis()
    guard = RedisRuntimeAuthorizationReplayGuard(client, key_prefix="pmr:")

    asyncio.run(guard.close())

    assert client.closed is True


def test_redis_replay_guard_requires_a_key_prefix() -> None:
    with pytest.raises(ValueError, match="prefix"):
        RedisRuntimeAuthorizationReplayGuard(_FakeRedis(), key_prefix="")
