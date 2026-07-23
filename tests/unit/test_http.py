"""Behavior tests for the ``POST /route`` HTTP entrypoint, using the shipped routing policy."""

import json
import sys
import types
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
import structlog.contextvars
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from policy_model_router.entrypoints import http as http_module
from policy_model_router.entrypoints.http import app

_SHIPPED_POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "routing_policy.yaml"
_TEST_AGENT_NAME = "financeiro-agent"
_TEST_API_KEY = "test-api-key"
_OTHER_AGENT_NAME = "other-agent"
_OTHER_API_KEY = "other-api-key"
_API_KEYS_JSON = json.dumps({_TEST_AGENT_NAME: _TEST_API_KEY, _OTHER_AGENT_NAME: _OTHER_API_KEY})
_AUTH_HEADERS = {"X-API-Key": _TEST_API_KEY}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    """A TestClient wired to the real shipped routing policy, triggering FastAPI's lifespan."""
    monkeypatch.setenv("ROUTING_POLICY_PATH", str(_SHIPPED_POLICY_PATH))
    monkeypatch.setenv("API_KEYS", _API_KEYS_JSON)
    monkeypatch.delenv("REDIS_URL", raising=False)
    with TestClient(app) as test_client:
        yield test_client


def _valid_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "requested_at": "2026-07-19T12:00:00Z",
        "workflow_id": "workflow-1",
        "task_id": "task-1",
        "agent_name": _TEST_AGENT_NAME,
        "workload": "cashflow_analysis",
        "risk_level": "high",
        "data_classification": "internal",
        "context_tokens_estimated": 1_000,
        "max_output_tokens_estimated": 500,
        "structured_output_required": False,
        "max_latency_ms": 60_000,
        "max_cost_usd": 1.0,
    }
    payload.update(overrides)
    return payload


def test_startup_configures_structured_logging(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, str]] = []
    monkeypatch.setattr(http_module, "configure_logging", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setenv("ROUTING_POLICY_PATH", str(_SHIPPED_POLICY_PATH))
    monkeypatch.setenv("API_KEYS", _API_KEYS_JSON)
    monkeypatch.delenv("REDIS_URL", raising=False)

    with TestClient(app):
        pass

    assert len(calls) == 1
    assert calls[0]["service"] == "policy-model-router"
    assert calls[0]["environment"]
    assert calls[0]["version"]


def test_startup_fails_closed_when_api_keys_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROUTING_POLICY_PATH", str(_SHIPPED_POLICY_PATH))
    monkeypatch.delenv("API_KEYS", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(RuntimeError, match="API_KEYS"), TestClient(app):
        pass


def test_startup_fails_closed_when_api_keys_is_malformed_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROUTING_POLICY_PATH", str(_SHIPPED_POLICY_PATH))
    monkeypatch.setenv("API_KEYS", "not valid json")
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(RuntimeError, match="API_KEYS"), TestClient(app):
        pass


def test_startup_fails_closed_when_api_keys_is_not_an_object_of_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROUTING_POLICY_PATH", str(_SHIPPED_POLICY_PATH))
    monkeypatch.setenv("API_KEYS", json.dumps(["not", "an", "object"]))
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(RuntimeError, match="API_KEYS"), TestClient(app):
        pass


def test_startup_fails_closed_when_api_keys_has_an_empty_agent_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROUTING_POLICY_PATH", str(_SHIPPED_POLICY_PATH))
    monkeypatch.setenv("API_KEYS", json.dumps({"": _TEST_API_KEY}))
    monkeypatch.delenv("REDIS_URL", raising=False)

    with pytest.raises(RuntimeError, match="API_KEYS"), TestClient(app):
        pass


def test_startup_fails_closed_when_rate_limiter_backend_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured but unreachable REDIS_URL must stop startup, not degrade silently."""
    fake_redis_module = types.ModuleType("redis")
    fake_asyncio_module = types.ModuleType("redis.asyncio")

    class _UnreachableClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        @classmethod
        def from_url(cls, *_args: object, **_kwargs: object) -> "_UnreachableClient":
            return cls()

        async def ping(self) -> None:
            raise ConnectionError("redis unreachable")

    fake_asyncio_module.Redis = _UnreachableClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "redis", fake_redis_module)
    monkeypatch.setitem(sys.modules, "redis.asyncio", fake_asyncio_module)

    monkeypatch.setenv("ROUTING_POLICY_PATH", str(_SHIPPED_POLICY_PATH))
    monkeypatch.setenv("API_KEYS", _API_KEYS_JSON)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    with pytest.raises(RuntimeError, match="rate limiter backend"), TestClient(app):
        pass


def test_shutdown_closes_the_redis_rate_limiter_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both rate-limit tiers' Redis connections must be released on graceful shutdown."""
    fake_redis_module = types.ModuleType("redis")
    fake_asyncio_module = types.ModuleType("redis.asyncio")
    closed_clients: list[Any] = []

    class _TrackedClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        @classmethod
        def from_url(cls, *_args: object, **_kwargs: object) -> "_TrackedClient":
            return cls()

        async def ping(self) -> None:
            return

        async def aclose(self) -> None:
            closed_clients.append(self)

    fake_asyncio_module.Redis = _TrackedClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "redis", fake_redis_module)
    monkeypatch.setitem(sys.modules, "redis.asyncio", fake_asyncio_module)

    monkeypatch.setenv("ROUTING_POLICY_PATH", str(_SHIPPED_POLICY_PATH))
    monkeypatch.setenv("API_KEYS", _API_KEYS_JSON)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    with TestClient(app):
        pass

    assert len(closed_clients) == 2


def test_shutdown_still_closes_the_second_limiter_when_the_first_close_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure releasing one rate-limit tier's connection must not skip the other's."""
    fake_redis_module = types.ModuleType("redis")
    fake_asyncio_module = types.ModuleType("redis.asyncio")
    created_clients: list[Any] = []
    closed_clients: list[Any] = []

    class _FlakyOnCloseClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            created_clients.append(self)

        @classmethod
        def from_url(cls, *_args: object, **_kwargs: object) -> "_FlakyOnCloseClient":
            return cls()

        async def ping(self) -> None:
            return

        async def aclose(self) -> None:
            if self is created_clients[0]:
                raise ConnectionError("redis unreachable during shutdown")
            closed_clients.append(self)

    fake_asyncio_module.Redis = _FlakyOnCloseClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "redis", fake_redis_module)
    monkeypatch.setitem(sys.modules, "redis.asyncio", fake_asyncio_module)

    monkeypatch.setenv("ROUTING_POLICY_PATH", str(_SHIPPED_POLICY_PATH))
    monkeypatch.setenv("API_KEYS", _API_KEYS_JSON)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    with TestClient(app):
        pass

    assert len(created_clients) == 2
    assert closed_clients == [created_clients[1]]


def test_route_returns_the_decision_for_a_valid_request(client: TestClient) -> None:
    response = client.post("/route", json=_valid_payload(), headers=_AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["selected_model_group"] == "reasoning-medium"
    assert body["workflow_id"] == "workflow-1"
    assert body["task_id"] == "task-1"
    rejected_groups = {candidate["model_group"] for candidate in body["rejected_candidates"]}
    assert rejected_groups == {"fast-small", "reasoning-strong", "fast-structured-output"}
    for candidate in body["rejected_candidates"]:
        assert candidate["reason_code"]
        assert candidate["observed_value"]
        assert candidate["required_value"]
    reasoning_strong = next(
        c for c in body["rejected_candidates"] if c["model_group"] == "reasoning-strong"
    )
    assert reasoning_strong["reason_code"] == "workload_mapped_elsewhere"
    assert body["policy_id"]
    assert body["policy_version"]
    assert body["policy_digest"].startswith("sha256:")
    assert body["service_version"]
    assert body["environment"]


def test_route_emits_a_routing_decision_log_event_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uses its own TestClient/capture_logs pair, entered together: the ``client`` fixture would
    already have run the lifespan's ``configure_logging()`` before ``capture_logs`` could patch
    structlog's processor chain, so no log line would be captured (see the sibling correlation-id
    test for the same reason).
    """
    monkeypatch.setenv("ROUTING_POLICY_PATH", str(_SHIPPED_POLICY_PATH))
    monkeypatch.setenv("API_KEYS", _API_KEYS_JSON)
    monkeypatch.delenv("REDIS_URL", raising=False)

    with TestClient(app) as test_client, capture_logs() as logs:
        response = test_client.post("/route", json=_valid_payload(), headers=_AUTH_HEADERS)

    assert response.status_code == 200
    decision_logs = [log for log in logs if log["event"] == "routing_decision"]
    assert len(decision_logs) == 1
    log = decision_logs[0]
    assert log["outcome"] == "accepted"
    assert log["model_group"] == "reasoning-medium"
    assert log["workflow_id"] == "workflow-1"
    assert log["task_id"] == "task-1"
    assert log["routing_decision_id"]
    assert log["policy_id"]


def test_route_emits_a_routing_decision_log_event_on_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROUTING_POLICY_PATH", str(_SHIPPED_POLICY_PATH))
    monkeypatch.setenv("API_KEYS", _API_KEYS_JSON)
    monkeypatch.delenv("REDIS_URL", raising=False)

    with TestClient(app) as test_client, capture_logs() as logs:
        response = test_client.post(
            "/route",
            json=_valid_payload(workload="document_extraction", data_classification="confidential"),
            headers=_AUTH_HEADERS,
        )

    assert response.status_code == 422
    decision_logs = [log for log in logs if log["event"] == "routing_decision"]
    assert len(decision_logs) == 1
    log = decision_logs[0]
    assert log["outcome"] == "rejected"
    assert log["model_group"] == "fast-small"
    assert log["reason_code"] == "data_classification_not_authorized"
    assert log["routing_decision_id"]
    assert log["policy_id"]


def test_route_response_never_reveals_other_agents_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test for the agent-allowlist leak: an authenticated agent must never learn
    which other agents are allowlisted for a restricted model group, via any field of any
    candidate in an otherwise-successful ``/route`` response - mirroring the guarantee
    ``_authenticate`` already makes on the auth-failure path.
    """
    restricted_policy_text = _SHIPPED_POLICY_PATH.read_text().replace(
        "  fast-small:\n    authorized_data_classifications: [public, internal]\n"
        "    authorized_risk_levels: [low, medium]\n"
        "    supports_structured_output: false\n"
        "    supports_tool_calling: true\n"
        "    max_context_tokens: 16000\n"
        "    typical_latency_ms: 3000\n"
        '    input_cost_usd_per_million_tokens: "0.10"\n'
        '    output_cost_usd_per_million_tokens: "0.40"\n'
        "    available: true\n"
        "    allowed_agents: []\n",
        "  fast-small:\n    authorized_data_classifications: [public, internal]\n"
        "    authorized_risk_levels: [low, medium]\n"
        "    supports_structured_output: false\n"
        "    supports_tool_calling: true\n"
        "    max_context_tokens: 16000\n"
        "    typical_latency_ms: 3000\n"
        '    input_cost_usd_per_million_tokens: "0.10"\n'
        '    output_cost_usd_per_million_tokens: "0.40"\n'
        "    available: true\n"
        "    allowed_agents: [secret-internal-agent, another-restricted-agent]\n",
    )
    assert "allowed_agents: [secret-internal-agent" in restricted_policy_text
    policy_path = tmp_path / "routing_policy.yaml"
    policy_path.write_text(restricted_policy_text, encoding="utf-8")

    monkeypatch.setenv("ROUTING_POLICY_PATH", str(policy_path))
    monkeypatch.setenv("API_KEYS", _API_KEYS_JSON)
    monkeypatch.delenv("REDIS_URL", raising=False)

    with TestClient(app) as restricted_client:
        response = restricted_client.post(
            "/route",
            json=_valid_payload(risk_level="low", data_classification="public"),
            headers=_AUTH_HEADERS,
        )

    assert response.status_code == 200
    assert "secret-internal-agent" not in response.text
    assert "another-restricted-agent" not in response.text
    fast_small = next(
        c for c in response.json()["rejected_candidates"] if c["model_group"] == "fast-small"
    )
    assert fast_small["reason_code"] == "agent_not_allowed"


def test_route_returns_a_stable_error_envelope_for_an_invalid_request(client: TestClient) -> None:
    response = client.post(
        "/route", json=_valid_payload(workload="not_a_real_workload"), headers=_AUTH_HEADERS
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "invalid_request",
            "message": "the request body does not match the expected schema",
        }
    }


def test_route_returns_a_stable_error_envelope_when_no_group_is_viable(client: TestClient) -> None:
    response = client.post(
        "/route",
        json=_valid_payload(workload="document_extraction", data_classification="confidential"),
        headers=_AUTH_HEADERS,
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "no_viable_model_group"
    assert "fast-small" in body["error"]["message"]

    decision = body["decision"]
    assert decision["workload"] == "document_extraction"
    assert decision["rejected_model_group"] == "fast-small"
    assert decision["reason_code"] == "data_classification_not_authorized"
    assert decision["observed_value"]
    assert decision["required_value"]
    assert decision["routing_decision_id"]
    assert decision["decided_at"]
    assert decision["policy_id"]
    assert decision["policy_version"]
    assert decision["policy_digest"].startswith("sha256:")
    assert decision["service_version"]
    assert decision["environment"]


def test_route_rejects_a_request_missing_the_api_key(client: TestClient) -> None:
    response = client.post("/route", json=_valid_payload())

    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "unauthorized", "message": "missing or invalid API key"}
    }


def test_route_rejects_a_request_with_the_wrong_api_key(client: TestClient) -> None:
    response = client.post("/route", json=_valid_payload(), headers={"X-API-Key": "wrong-key"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_route_rejects_an_agent_using_a_different_agents_key(client: TestClient) -> None:
    """Per-agent keys (ADR-0007's amendment): one agent's key must not authorize another agent."""
    response = client.post("/route", json=_valid_payload(), headers={"X-API-Key": _OTHER_API_KEY})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_route_rejects_a_request_from_an_unconfigured_agent(client: TestClient) -> None:
    response = client.post(
        "/route",
        json=_valid_payload(agent_name="unconfigured-agent"),
        headers={"X-API-Key": _TEST_API_KEY},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_route_is_rate_limited_per_client_and_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ROUTING_POLICY_PATH", str(_SHIPPED_POLICY_PATH))
    monkeypatch.setenv("API_KEYS", _API_KEYS_JSON)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("RATE_LIMIT_MAX_REQUESTS", "1")

    with TestClient(app) as rate_limited_client:
        first = rate_limited_client.post("/route", json=_valid_payload(), headers=_AUTH_HEADERS)
        second = rate_limited_client.post("/route", json=_valid_payload(), headers=_AUTH_HEADERS)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json() == {
        "error": {
            "code": "rate_limit_exceeded",
            "message": "too many requests, try again later",
        }
    }


def test_invalid_api_key_attempts_are_rate_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    """Rate limiting runs before authentication, so brute-forcing a key is throttled too."""
    monkeypatch.setenv("ROUTING_POLICY_PATH", str(_SHIPPED_POLICY_PATH))
    monkeypatch.setenv("API_KEYS", _API_KEYS_JSON)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("RATE_LIMIT_MAX_REQUESTS", "1")
    wrong_key_headers = {"X-API-Key": "wrong-key"}

    with TestClient(app) as rate_limited_client:
        first = rate_limited_client.post("/route", json=_valid_payload(), headers=wrong_key_headers)
        second = rate_limited_client.post(
            "/route", json=_valid_payload(), headers=wrong_key_headers
        )

    assert first.status_code == 401
    assert second.status_code == 429


def test_route_is_rate_limited_per_client_ip_even_when_agent_name_varies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The per-IP tier closes the bypass where an attacker varies agent_name per attempt."""
    monkeypatch.setenv("ROUTING_POLICY_PATH", str(_SHIPPED_POLICY_PATH))
    monkeypatch.setenv("API_KEYS", _API_KEYS_JSON)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("RATE_LIMIT_PER_IP_MAX_REQUESTS", "1")
    wrong_key_headers = {"X-API-Key": "wrong-key"}

    with TestClient(app) as rate_limited_client:
        first = rate_limited_client.post(
            "/route",
            json=_valid_payload(agent_name="agent-one"),
            headers=wrong_key_headers,
        )
        second = rate_limited_client.post(
            "/route",
            json=_valid_payload(agent_name="agent-two"),
            headers=wrong_key_headers,
        )

    assert first.status_code == 401
    assert second.status_code == 429


def test_route_rejects_a_body_over_the_configured_size_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROUTING_POLICY_PATH", str(_SHIPPED_POLICY_PATH))
    monkeypatch.setenv("API_KEYS", _API_KEYS_JSON)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "10")

    with TestClient(app) as size_limited_client:
        response = size_limited_client.post("/route", json=_valid_payload(), headers=_AUTH_HEADERS)

    assert response.status_code == 413
    assert response.json() == {
        "error": {
            "code": "payload_too_large",
            "message": "request body exceeds the 10-byte limit",
        }
    }


def test_an_oversized_body_still_consumes_the_per_ip_rate_limit_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test: a flood of oversized bodies must not get a free pass on the IP tier just
    because the body-size check runs after it - the IP check must still see (and count) the
    request, closing the gap where a malformed/oversized body used to bypass both rate-limit tiers
    entirely (ADR-0011).
    """
    monkeypatch.setenv("ROUTING_POLICY_PATH", str(_SHIPPED_POLICY_PATH))
    monkeypatch.setenv("API_KEYS", _API_KEYS_JSON)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "10")
    monkeypatch.setenv("RATE_LIMIT_PER_IP_MAX_REQUESTS", "1")

    with TestClient(app) as limited_client:
        first = limited_client.post("/route", json=_valid_payload(), headers=_AUTH_HEADERS)
        second = limited_client.post("/route", json=_valid_payload(), headers=_AUTH_HEADERS)

    assert first.status_code == 413
    assert second.status_code == 429


def test_route_rejects_an_identifier_over_the_max_length(client: TestClient) -> None:
    response = client.post(
        "/route",
        json=_valid_payload(workflow_id="w" * 201),
        headers=_AUTH_HEADERS,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_route_rejects_a_token_estimate_over_the_ceiling(client: TestClient) -> None:
    response = client.post(
        "/route",
        json=_valid_payload(context_tokens_estimated=10_000_001),
        headers=_AUTH_HEADERS,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_an_oversized_correlation_id_header_is_ignored(client: TestClient) -> None:
    oversized_correlation_id = "c" * 201

    response = client.post(
        "/route",
        json=_valid_payload(),
        headers={**_AUTH_HEADERS, "X-Correlation-Id": oversized_correlation_id},
    )

    assert response.status_code == 200
    assert response.headers["X-Correlation-Id"] != oversized_correlation_id
    assert len(response.headers["X-Correlation-Id"]) < 201


def test_health_endpoint_requires_no_api_key(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_endpoint_requires_no_api_key(client: TestClient) -> None:
    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_metrics_endpoint_requires_no_api_key(client: TestClient) -> None:
    response = client.get("/metrics")

    assert response.status_code == 200
    assert "text/plain" in response.headers["content-type"]
    assert "policy_model_router_rate_limiter_backend_unavailable_total" in response.text


def test_metrics_endpoint_includes_route_metrics_after_a_request(client: TestClient) -> None:
    client.post("/route", json=_valid_payload(), headers=_AUTH_HEADERS)

    response = client.get("/metrics")

    assert "policy_model_router_route_decisions_total" in response.text
    assert "policy_model_router_route_duration_seconds" in response.text
    assert "policy_model_router_route_rejections_total" in response.text
    assert "policy_model_router_rate_limit_decisions_total" in response.text


def test_route_echoes_the_callers_correlation_id(client: TestClient) -> None:
    response = client.post(
        "/route",
        json=_valid_payload(),
        headers={**_AUTH_HEADERS, "X-Correlation-Id": "caller-supplied-id"},
    )

    assert response.headers["X-Correlation-Id"] == "caller-supplied-id"


def test_route_generates_a_correlation_id_when_the_caller_sends_none(client: TestClient) -> None:
    response = client.post("/route", json=_valid_payload(), headers=_AUTH_HEADERS)

    assert response.headers["X-Correlation-Id"]


def test_health_endpoint_includes_a_correlation_id_header(client: TestClient) -> None:
    response = client.get("/health")

    assert response.headers["X-Correlation-Id"]


def test_correlation_id_is_bound_to_log_lines_emitted_during_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A log line from an adapter mid-request (Redis fail-open) must carry the request's own ID."""
    fake_redis_module = types.ModuleType("redis")
    fake_asyncio_module = types.ModuleType("redis.asyncio")

    class _FlakyClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        @classmethod
        def from_url(cls, *_args: object, **_kwargs: object) -> "_FlakyClient":
            return cls()

        async def ping(self) -> None:
            return

        async def eval(self, *_args: object, **_kwargs: object) -> int:
            raise ConnectionError("redis unreachable")

        async def aclose(self) -> None:
            return

    fake_asyncio_module.Redis = _FlakyClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "redis", fake_redis_module)
    monkeypatch.setitem(sys.modules, "redis.asyncio", fake_asyncio_module)

    monkeypatch.setenv("ROUTING_POLICY_PATH", str(_SHIPPED_POLICY_PATH))
    monkeypatch.setenv("API_KEYS", _API_KEYS_JSON)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")

    with (
        TestClient(app) as flaky_client,
        capture_logs(processors=[structlog.contextvars.merge_contextvars]) as logs,
    ):
        response = flaky_client.post(
            "/route",
            json=_valid_payload(),
            headers={**_AUTH_HEADERS, "X-Correlation-Id": "trace-me-123"},
        )

    assert response.status_code == 200
    backend_logs = [log for log in logs if log["event"] == "rate_limiter_backend_unavailable"]
    assert backend_logs
    assert all(log["correlation_id"] == "trace-me-123" for log in backend_logs)


def test_openapi_docs_are_disabled_by_default(client: TestClient) -> None:
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_api_docs_enabled_defaults_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENABLE_API_DOCS", raising=False)

    assert http_module._api_docs_enabled() is False


def test_api_docs_enabled_reads_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_API_DOCS", "true")

    assert http_module._api_docs_enabled() is True
