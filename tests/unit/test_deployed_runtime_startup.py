"""Deployed startup composition using real enforcement and controlled Redis clients.

Only the external Redis and telemetry boundaries are replaced. These tests do not certify a live
TLS connection, Redis ACLs, or an end-to-end Gateway/Governance deployment.
"""

import base64
import json
import os
import sys
import types
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from a2a_otel_kit import ObservabilitySettings
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from fastapi.testclient import TestClient
from opentelemetry.trace import INVALID_SPAN_CONTEXT, NonRecordingSpan, Span
from starlette.datastructures import State
from structlog.testing import capture_logs

from policy_model_router.adapters.clock import SystemClock
from policy_model_router.adapters.id_generator import Uuid4IdGenerator
from policy_model_router.application.runtime_authorization import RuntimeAuthorizationVerifier
from policy_model_router.domain.runtime_authorization import RuntimeAuthorizationError
from policy_model_router.entrypoints import http as http_module
from policy_model_router.entrypoints.runtime_authorization_factory import (
    DisabledRuntimeAuthorizationVerifier,
    build_runtime_authorization_verifier,
)
from policy_model_router.entrypoints.runtime_authorization_settings import (
    RuntimeAuthorizationSettings,
)
from policy_model_router.entrypoints.runtime_control_settings import RuntimeControlSettings
from policy_model_router.entrypoints.settings import Settings
from tests.unit.test_runtime_authorization import AGENT_ID, NOW, _claims, _route_request, _signed

_AGENT_NAME = _route_request().agent_name
_API_KEY = "synthetic-startup-api-key"
_REDIS_URL = "rediss://redis.example.invalid:6379/0"
_CONTROL_PREFIX = "synthetic:runtime-control:"
_REPLAY_PREFIX = "synthetic:runtime-auth:"
_FAILURE_MARKER = "synthetic-redis-client-detail-not-for-publication"
_SHIPPED_POLICY = Path(__file__).resolve().parents[2] / "config" / "routing_policy.yaml"


def _snapshot(*, state: str = "inactive", revoked_through: int = 0) -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "agent_id": str(AGENT_ID),
            "control_epoch": 3,
            "state": state,
            "revoked_through_agent_version": revoked_through,
            "transition_id": None,
        }
    )


class _RedisBoundary:
    """Track only external commands; production adapters retain their actual semantics."""

    def __init__(self) -> None:
        self.clients: list[_RedisClient] = []
        self.events: list[tuple[str, str]] = []
        self.consumed: set[str] = set()
        self.snapshot: str | None = _snapshot()
        self.fail_ping_index: int | None = None
        self.fail_get = False
        self.fail_set = False

    def from_url(self, url: str, **options: object) -> "_RedisClient":
        client = _RedisClient(self, url, options, len(self.clients))
        self.clients.append(client)
        return client


@dataclass
class _RedisClient:
    boundary: _RedisBoundary
    url: str
    options: dict[str, object]
    index: int
    ping_calls: int = 0
    close_calls: int = 0

    async def ping(self) -> bool:
        self.ping_calls += 1
        if self.index == self.boundary.fail_ping_index:
            raise ConnectionError(_FAILURE_MARKER)
        return True

    async def aclose(self) -> None:
        self.close_calls += 1

    async def get(self, key: str) -> str | None:
        self.boundary.events.append(("get", key))
        if self.boundary.fail_get:
            raise ConnectionError(_FAILURE_MARKER)
        return self.boundary.snapshot

    async def set(self, key: str, value: str, *, nx: bool, ex: int) -> bool | None:
        assert value == "1" and nx is True and ex > 0
        self.boundary.events.append(("set", key))
        if self.boundary.fail_set:
            raise ConnectionError(_FAILURE_MARKER)
        if key in self.boundary.consumed:
            return None
        self.boundary.consumed.add(key)
        return True

    async def eval(self, script: str, numkeys: int, key: str, window_ms: int) -> int:
        assert script and numkeys == 1 and key and window_ms > 0
        return 1


class _ObservabilityBoundary:
    def __init__(self) -> None:
        self.settings: list[ObservabilitySettings] = []
        self.shutdown_calls = 0

    def start_span(self, name: str, **_options: object) -> AbstractContextManager[Span]:
        assert name == "policy_model_router.http.request"
        return nullcontext(NonRecordingSpan(INVALID_SPAN_CONTEXT))

    def shutdown(self) -> None:
        self.shutdown_calls += 1


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear relevant variable names without reading or printing their original values."""
    core_names = {name.upper() for name in Settings.model_fields} | {"API_KEYS"}
    for name in tuple(os.environ):
        if name.upper() in core_names or name.upper().startswith(
            ("RUNTIME_AUTHORIZATION_", "RUNTIME_CONTROL_")
        ):
            monkeypatch.delenv(name)


@pytest.fixture
def redis_boundary(monkeypatch: pytest.MonkeyPatch) -> _RedisBoundary:
    boundary = _RedisBoundary()
    redis_module = types.ModuleType("redis")
    asyncio_module = types.ModuleType("redis.asyncio")
    asyncio_module.Redis = boundary  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "redis", redis_module)
    monkeypatch.setitem(sys.modules, "redis.asyncio", asyncio_module)
    return boundary


@pytest.fixture
def private_key() -> Ed25519PrivateKey:
    """Deterministic synthetic signing material, never loaded from the user's credentials."""
    return Ed25519PrivateKey.from_private_bytes(bytes(range(32)))


@pytest.fixture
def public_key_path(tmp_path: Path, private_key: Ed25519PrivateKey) -> Path:
    public_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    document = {
        "schema_version": "1.0",
        "generation": 1,
        "keys": [
            {
                "kid": "gov-ed25519-2026-08",
                "status": "active",
                "not_before": (NOW - timedelta(days=1)).isoformat(),
                "verify_until": (NOW + timedelta(days=1)).isoformat(),
                "jwk": {
                    "kty": "OKP",
                    "crv": "Ed25519",
                    "x": base64.urlsafe_b64encode(public_bytes).decode("ascii").rstrip("="),
                },
            }
        ],
    }
    path = tmp_path / "public-trust-set.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@pytest.fixture(params=["staging", "production"])
def deployed_env(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, public_key_path: Path
) -> str:
    """Configure the actual settings parser with a complete synthetic deployed configuration."""
    environment = str(request.param)
    values = {
        "APP_ENV": environment,
        "ROUTING_POLICY_PATH": str(_SHIPPED_POLICY),
        "API_KEYS": json.dumps({_AGENT_NAME: _API_KEY}),
        "REDIS_URL": _REDIS_URL,
        "RATE_LIMIT_FINGERPRINT_SECRET": "synthetic-fingerprint-secret",
        "RUNTIME_AUTHORIZATION_REQUIRED": "true",
        "RUNTIME_CONTROL_REQUIRED": "true",
        "RUNTIME_AUTHORIZATION_TRUSTED_KEY_SET_PATH": str(public_key_path),
        "RUNTIME_AUTHORIZATION_AGENT_BINDINGS_JSON": json.dumps({_AGENT_NAME: str(AGENT_ID)}),
        "RUNTIME_AUTHORIZATION_EXPECTED_POLICY_DIGEST": "d" * 64,
        "RUNTIME_AUTHORIZATION_EXPECTED_CONTROL_CATALOG_DIGEST": "e" * 64,
        "RUNTIME_AUTHORIZATION_REPLAY_KEY_PREFIX": _REPLAY_PREFIX,
        "RUNTIME_CONTROL_REDIS_KEY_PREFIX": _CONTROL_PREFIX,
        "RUNTIME_CONTROL_TIMEOUT_SECONDS": "0.75",
        "RUNTIME_CONTROL_MAX_SNAPSHOT_BYTES": "1024",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    return environment


def _build(environment: str) -> RuntimeAuthorizationVerifier | DisabledRuntimeAuthorizationVerifier:
    return build_runtime_authorization_verifier(
        RuntimeAuthorizationSettings(),
        app_env=environment,
        redis_url=Settings().redis_url,
        runtime_control_settings=RuntimeControlSettings(),
    )


@pytest.fixture
def http_boundaries(monkeypatch: pytest.MonkeyPatch) -> _ObservabilityBoundary:
    boundary = _ObservabilityBoundary()

    class _Factory:
        @staticmethod
        def configure(settings: ObservabilitySettings) -> _ObservabilityBoundary:
            boundary.settings.append(settings)
            return boundary

    def now(_clock: object) -> datetime:
        return NOW + timedelta(seconds=1)

    monkeypatch.setattr(http_module, "Observability", _Factory)
    monkeypatch.setattr(http_module.app, "state", State())
    monkeypatch.setattr(http_module, "_install_reload_signal_handler", lambda _app: True)
    monkeypatch.setattr(http_module, "_remove_reload_signal_handler", lambda: None)
    monkeypatch.setattr(SystemClock, "now", now)
    monkeypatch.setattr(
        Uuid4IdGenerator,
        "new_id",
        lambda _generator: "77777777-7777-4777-8777-777777777777",
    )
    return boundary


def _wire_request() -> dict[str, object]:
    request = _route_request()
    return {
        "schema_version": request.schema_version,
        "requested_at": request.requested_at.isoformat(),
        "workflow_id": request.workflow_id,
        "task_id": request.task_id,
        "agent_name": request.agent_name,
        "workload": request.workload.value,
        "risk_level": request.risk_level.value,
        "data_classification": request.data_classification.value,
        "context_tokens_estimated": request.context_tokens_estimated,
        "max_output_tokens_estimated": request.max_output_tokens_estimated,
        "structured_output_required": request.structured_output_required,
        "max_latency_ms": request.max_latency_ms,
        "max_cost_usd": str(request.max_cost_usd),
    }


@pytest.mark.anyio
async def test_deployed_factory_builds_shared_enforcement_with_bounded_clients(
    deployed_env: str, redis_boundary: _RedisBoundary, private_key: Ed25519PrivateKey
) -> None:
    verifier = _build(deployed_env)
    assert isinstance(verifier, RuntimeAuthorizationVerifier)
    await verifier.ping()
    verified = await verifier.verify(_signed(private_key), _route_request(), now=NOW)
    assert verified.authorization_id == _signed(private_key).claims.authorization_id
    await verifier.close()

    assert len(redis_boundary.clients) == 2
    replay, control = redis_boundary.clients
    assert replay.url == control.url == _REDIS_URL
    assert replay.options == {"socket_connect_timeout": 2.0, "socket_timeout": 2.0}
    assert control.options == {
        "decode_responses": True,
        "socket_connect_timeout": 0.75,
        "socket_timeout": 0.75,
    }
    assert [client.ping_calls for client in redis_boundary.clients] == [1, 1]
    assert [client.close_calls for client in redis_boundary.clients] == [1, 1]
    assert [operation for operation, _key in redis_boundary.events] == ["get", "set"]
    assert redis_boundary.events[0][1] == f"{_CONTROL_PREFIX}{AGENT_ID}"
    assert redis_boundary.events[1][1].startswith(_REPLAY_PREFIX)


@pytest.mark.parametrize("switch", ["RUNTIME_AUTHORIZATION_REQUIRED", "RUNTIME_CONTROL_REQUIRED"])
def test_deployed_factory_refuses_disabled_enforcement(
    deployed_env: str, redis_boundary: _RedisBoundary, monkeypatch: pytest.MonkeyPatch, switch: str
) -> None:
    monkeypatch.setenv(switch, "false")
    with pytest.raises(RuntimeError, match=switch):
        _build(deployed_env)
    assert not redis_boundary.clients


@pytest.mark.parametrize(
    "name",
    [
        "RUNTIME_AUTHORIZATION_EXPECTED_POLICY_DIGEST",
        "RUNTIME_AUTHORIZATION_EXPECTED_CONTROL_CATALOG_DIGEST",
    ],
)
@pytest.mark.parametrize(
    "value", ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64, "sha256:" + "a" * 64]
)
def test_deployed_factory_requires_exact_trust_digests(
    deployed_env: str,
    redis_boundary: _RedisBoundary,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=name):
        _build(deployed_env)
    assert not redis_boundary.clients


@pytest.mark.parametrize("value", ["not-json", "{}", "[]", '{"agent":"not-a-uuid"}', '{"agent":7}'])
def test_deployed_factory_refuses_invalid_agent_bindings(
    deployed_env: str, redis_boundary: _RedisBoundary, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("RUNTIME_AUTHORIZATION_AGENT_BINDINGS_JSON", value)
    with pytest.raises(RuntimeAuthorizationError) as error:
        _build(deployed_env)
    assert error.value.code == "invalid_agent_bindings"
    assert not redis_boundary.clients


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("missing-file", "key_set_unavailable"),
        ("invalid-json", "invalid_key_set"),
        ("empty-keys", "invalid_key_set"),
        ("private-field", "invalid_key_set"),
        ("oversized", "key_set_too_large"),
    ],
)
def test_deployed_factory_loads_a_bounded_public_only_trust_set(
    deployed_env: str,
    redis_boundary: _RedisBoundary,
    monkeypatch: pytest.MonkeyPatch,
    public_key_path: Path,
    case: str,
    expected_code: str,
) -> None:
    if case == "missing-file":
        monkeypatch.setenv(
            "RUNTIME_AUTHORIZATION_TRUSTED_KEY_SET_PATH",
            str(public_key_path.parent / "missing.json"),
        )
    elif case == "invalid-json":
        public_key_path.write_text("not-json", encoding="utf-8")
    elif case == "empty-keys":
        public_key_path.write_text(
            json.dumps({"schema_version": "1.0", "generation": 1, "keys": []}), encoding="utf-8"
        )
    elif case == "private-field":
        document = json.loads(public_key_path.read_text(encoding="utf-8"))
        document["keys"][0]["jwk"]["d"] = "synthetic-private-field-rejected"
        public_key_path.write_text(json.dumps(document), encoding="utf-8")
    else:
        monkeypatch.setenv("RUNTIME_AUTHORIZATION_MAX_KEY_SET_BYTES", "1024")
        public_key_path.write_text(" " * 1025, encoding="utf-8")
    with pytest.raises(RuntimeAuthorizationError) as error:
        _build(deployed_env)
    assert error.value.code == expected_code
    assert not redis_boundary.clients


@pytest.mark.parametrize("value", [None, ""])
def test_deployed_factory_requires_shared_replay_backend(
    deployed_env: str,
    redis_boundary: _RedisBoundary,
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
) -> None:
    if value is None:
        monkeypatch.delenv("REDIS_URL")
    else:
        monkeypatch.setenv("REDIS_URL", value)
    with pytest.raises(RuntimeError, match="distributed runtime authorization replay protection"):
        _build(deployed_env)
    assert not redis_boundary.clients


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("redis://redis.example.invalid:6379/0", "must use rediss://"),
        ("https://redis.example.invalid/0", "must be an absolute"),
        ("rediss:///0", "must be an absolute"),
        ("redis.example.invalid:6379/0", "must be an absolute"),
    ],
)
def test_deployed_factory_rejects_non_tls_or_malformed_runtime_control_url(
    deployed_env: str,
    redis_boundary: _RedisBoundary,
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    message: str,
) -> None:
    monkeypatch.setenv("REDIS_URL", url)
    with pytest.raises(RuntimeError, match=message):
        _build(deployed_env)
    assert not redis_boundary.events


def test_deployed_factory_requires_the_optional_redis_dependency(
    deployed_env: str, redis_boundary: _RedisBoundary, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "redis.asyncio", None)
    with pytest.raises(RuntimeError, match="redis is not installed"):
        _build(deployed_env)
    assert not redis_boundary.clients


def test_deployed_lifespan_does_not_serve_without_redis_dependency(
    deployed_env: str,
    redis_boundary: _RedisBoundary,
    http_boundaries: _ObservabilityBoundary,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "redis.asyncio", None)
    with pytest.raises(RuntimeError, match="redis is not installed"), TestClient(http_module.app):
        pytest.fail("startup admitted a missing shared-state dependency")
    assert not redis_boundary.clients


def test_deployed_lifespan_keeps_authentication_and_signed_enforcement_active(
    deployed_env: str, redis_boundary: _RedisBoundary, http_boundaries: _ObservabilityBoundary
) -> None:
    with TestClient(http_module.app) as client:
        assert client.get("/readyz").status_code == 200
        assert isinstance(
            http_module.app.state.runtime_authorization_verifier, RuntimeAuthorizationVerifier
        )
        assert http_module.app.state.runtime_control_settings.required is True
        unsigned = client.post("/route", json=_wire_request(), headers={"X-API-Key": _API_KEY})
        assert unsigned.status_code == 403
        assert unsigned.json()["error"]["code"] == "runtime_authorization_required"
        unauthenticated = client.post("/route", json=_wire_request())
        assert unauthenticated.status_code == 401
        assert unauthenticated.json()["error"]["code"] == "unauthorized"
        assert not redis_boundary.events

    assert len(redis_boundary.clients) == 4
    assert all(client.ping_calls == client.close_calls == 1 for client in redis_boundary.clients)
    assert http_boundaries.shutdown_calls == 1
    assert [settings.environment for settings in http_boundaries.settings] == [deployed_env]


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("RUNTIME_AUTHORIZATION_REQUIRED", "false", "RUNTIME_AUTHORIZATION_REQUIRED"),
        ("RUNTIME_CONTROL_REQUIRED", "false", "RUNTIME_CONTROL_REQUIRED"),
        ("RUNTIME_AUTHORIZATION_EXPECTED_POLICY_DIGEST", "", "EXPECTED_POLICY_DIGEST"),
        ("RUNTIME_AUTHORIZATION_TRUSTED_KEY_SET_PATH", "", "TRUSTED_KEY_SET_PATH"),
        ("REDIS_URL", "redis://redis.example.invalid:6379/0", "must use rediss://"),
    ],
)
def test_deployed_lifespan_does_not_serve_invalid_configuration(
    deployed_env: str,
    redis_boundary: _RedisBoundary,
    http_boundaries: _ObservabilityBoundary,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    message: str,
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(RuntimeError, match=message), TestClient(http_module.app):
        pytest.fail("startup admitted an invalid deployed configuration")
    assert not redis_boundary.events


@pytest.mark.parametrize(
    ("failed_index", "expected_code"),
    [(0, "replay_store_unavailable"), (1, "runtime_control_unavailable")],
)
def test_deployed_lifespan_denies_startup_when_enforcement_ping_fails(
    deployed_env: str,
    redis_boundary: _RedisBoundary,
    http_boundaries: _ObservabilityBoundary,
    failed_index: int,
    expected_code: str,
) -> None:
    redis_boundary.fail_ping_index = failed_index
    with pytest.raises(RuntimeAuthorizationError) as error, TestClient(http_module.app):
        pytest.fail("startup admitted an unreachable enforcement backend")
    assert error.value.code == expected_code
    assert _FAILURE_MARKER not in str(error.value)
    assert not redis_boundary.events


@pytest.mark.parametrize("failed_index", [2, 3])
def test_deployed_lifespan_still_requires_both_rate_limiter_backends(
    deployed_env: str,
    redis_boundary: _RedisBoundary,
    http_boundaries: _ObservabilityBoundary,
    failed_index: int,
) -> None:
    redis_boundary.fail_ping_index = failed_index
    with (
        pytest.raises(RuntimeError, match="rate limiter backend is not reachable") as error,
        TestClient(http_module.app),
    ):
        pytest.fail("startup admitted an unreachable rate limiter")
    assert _FAILURE_MARKER not in str(error.value)
    assert not redis_boundary.events


def test_deployed_http_composition_verifies_signature_control_and_single_use(
    deployed_env: str,
    redis_boundary: _RedisBoundary,
    http_boundaries: _ObservabilityBoundary,
    private_key: Ed25519PrivateKey,
) -> None:
    envelope = _signed(private_key).model_dump(mode="json")
    payload = {"request": _wire_request(), "authorization": envelope}
    with TestClient(http_module.app) as client, capture_logs() as logs:
        accepted = client.post("/route", json=payload, headers={"X-API-Key": _API_KEY})
        assert accepted.status_code == 200
        assert accepted.json()["selected_model_group"] == "reasoning-strong"
        duplicate = client.post("/route", json=payload, headers={"X-API-Key": _API_KEY})
        assert duplicate.status_code == 403
        assert duplicate.json()["error"]["code"] == "replay_detected"

    assert [operation for operation, _key in redis_boundary.events] == ["get", "set", "get", "set"]
    serialized_logs = json.dumps(logs)
    assert _API_KEY not in serialized_logs
    assert envelope["signature"] not in serialized_logs
    assert "signing_bytes" not in serialized_logs


@pytest.mark.parametrize("length_hint", [None, "1"])
def test_deployed_oversized_upload_does_not_consume_signed_authorization(
    deployed_env: str,
    redis_boundary: _RedisBoundary,
    http_boundaries: _ObservabilityBoundary,
    private_key: Ed25519PrivateKey,
    length_hint: str | None,
) -> None:
    """A rejected upload leaves the same valid authorization available for one smaller POST."""
    envelope = _signed(private_key).model_dump(mode="json")
    payload = {"request": _wire_request(), "authorization": envelope}
    body = json.dumps(payload).encode()
    headers = {
        "X-API-Key": _API_KEY,
        "Content-Type": "application/json",
        "X-Correlation-Id": "signed-body-admission-test",
    }
    if length_hint is not None:
        headers["Content-Length"] = length_hint
    with TestClient(http_module.app) as client, capture_logs() as logs:
        http_module.app.state.max_request_body_bytes = len(body)
        # A generator omits HTTPX's automatic Content-Length. TestClient itself may coalesce
        # frames; deterministic multi-frame composition is separately covered in test_http.py.
        oversized = client.post("/route", content=iter((body, b" ")), headers=headers)
        assert oversized.status_code == 413
        assert oversized.headers["X-Correlation-Id"] == "signed-body-admission-test"
        assert not redis_boundary.events
        assert not redis_boundary.consumed
        assert not any(
            entry["event"] in {"routing_decision", "runtime_violation"} for entry in logs
        )

        accepted = client.post("/route", content=body, headers=headers)
        assert accepted.status_code == 200
        assert [operation for operation, _key in redis_boundary.events] == ["get", "set"]
        duplicate = client.post("/route", content=body, headers=headers)
        assert duplicate.status_code == 403
        assert duplicate.json()["error"]["code"] == "replay_detected"

    assert len(redis_boundary.consumed) == 1
    serialized_logs = json.dumps(logs)
    assert _API_KEY not in serialized_logs
    assert envelope["signature"] not in serialized_logs


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("tampered-signature", "invalid_signature"),
        ("request-mismatch", "request_binding_mismatch"),
        ("agent-mismatch", "agent_binding_mismatch"),
        ("policy-mismatch", "governance_policy_mismatch"),
        ("catalog-mismatch", "governance_policy_mismatch"),
    ],
)
def test_deployed_http_rejects_untrusted_authorization_before_shared_state(
    deployed_env: str,
    redis_boundary: _RedisBoundary,
    http_boundaries: _ObservabilityBoundary,
    monkeypatch: pytest.MonkeyPatch,
    private_key: Ed25519PrivateKey,
    case: str,
    expected_code: str,
) -> None:
    envelope = _signed(private_key)
    request = _wire_request()
    if case == "tampered-signature":
        envelope = envelope.model_copy(
            update={"claims": envelope.claims.model_copy(update={"scope_digest": "f" * 64})}
        )
    elif case == "request-mismatch":
        request["max_latency_ms"] = 29_999
    elif case == "agent-mismatch":
        monkeypatch.setenv(
            "RUNTIME_AUTHORIZATION_AGENT_BINDINGS_JSON",
            json.dumps({_AGENT_NAME: "99999999-9999-4999-8999-999999999999"}),
        )
    else:
        suffix = "POLICY_DIGEST" if case == "policy-mismatch" else "CONTROL_CATALOG_DIGEST"
        monkeypatch.setenv(f"RUNTIME_AUTHORIZATION_EXPECTED_{suffix}", "f" * 64)
    with TestClient(http_module.app) as client:
        denied = client.post(
            "/route",
            json={"request": request, "authorization": envelope.model_dump(mode="json")},
            headers={"X-API-Key": _API_KEY},
        )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == expected_code
    assert not redis_boundary.events


def test_deployed_http_does_not_publish_a_group_outside_signed_scope(
    deployed_env: str,
    redis_boundary: _RedisBoundary,
    http_boundaries: _ObservabilityBoundary,
    private_key: Ed25519PrivateKey,
) -> None:
    claims = _claims()
    scope = claims.scope.model_copy(
        update={
            "models": (claims.scope.models[0].model_copy(update={"routing_group": "balanced"}),)
        }
    )
    envelope = _signed(private_key, claims=claims.model_copy(update={"scope": scope}))
    with TestClient(http_module.app) as client, capture_logs() as logs:
        denied = client.post(
            "/route",
            json={"request": _wire_request(), "authorization": envelope.model_dump(mode="json")},
            headers={"X-API-Key": _API_KEY},
        )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "selected_model_group_not_authorized"
    assert [operation for operation, _key in redis_boundary.events] == ["get", "set"]
    assert "selected_model_group" not in denied.json()
    assert not any(log.get("outcome") == "accepted" for log in logs)


@pytest.mark.parametrize(
    ("snapshot", "expected_code"),
    [
        (_snapshot(state="active"), "kill_switch_engaged"),
        (_snapshot(revoked_through=7), "runtime_authorization_revoked"),
        (None, "runtime_control_unavailable"),
        ("not-json", "runtime_control_unavailable"),
        (" " * 1025, "runtime_control_unavailable"),
    ],
)
def test_deployed_http_runtime_control_denies_before_replay_consumption(
    deployed_env: str,
    redis_boundary: _RedisBoundary,
    http_boundaries: _ObservabilityBoundary,
    private_key: Ed25519PrivateKey,
    snapshot: str | None,
    expected_code: str,
) -> None:
    redis_boundary.snapshot = snapshot
    payload = {
        "request": _wire_request(),
        "authorization": _signed(private_key).model_dump(mode="json"),
    }
    with TestClient(http_module.app) as client:
        denied = client.post("/route", json=payload, headers={"X-API-Key": _API_KEY})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == expected_code
    assert redis_boundary.events == [("get", f"{_CONTROL_PREFIX}{AGENT_ID}")]
    assert not redis_boundary.consumed


@pytest.mark.parametrize(
    ("failed_command", "code"),
    [("get", "runtime_control_unavailable"), ("set", "replay_store_unavailable")],
)
def test_deployed_http_runtime_dependency_outage_is_not_rate_limiter_fail_open(
    deployed_env: str,
    redis_boundary: _RedisBoundary,
    http_boundaries: _ObservabilityBoundary,
    private_key: Ed25519PrivateKey,
    failed_command: str,
    code: str,
) -> None:
    payload = {
        "request": _wire_request(),
        "authorization": _signed(private_key).model_dump(mode="json"),
    }
    with TestClient(http_module.app) as client, capture_logs() as logs:
        redis_boundary.fail_get = failed_command == "get"
        redis_boundary.fail_set = failed_command == "set"
        denied = client.post("/route", json=payload, headers={"X-API-Key": _API_KEY})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == code
    assert _FAILURE_MARKER not in denied.text
    assert _FAILURE_MARKER not in json.dumps(logs)
    assert not redis_boundary.consumed
    assert not any(log.get("outcome") == "accepted" for log in logs)
