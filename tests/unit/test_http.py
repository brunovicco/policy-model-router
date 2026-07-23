"""Behavior tests for the ``POST /route`` HTTP entrypoint, using the shipped routing policy."""

import json
import sys
import types
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

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


def test_route_returns_the_decision_for_a_valid_request(client: TestClient) -> None:
    response = client.post("/route", json=_valid_payload(), headers=_AUTH_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["selected_model_group"] == "reasoning-medium"
    assert body["workflow_id"] == "workflow-1"
    assert body["task_id"] == "task-1"
    rejected_groups = {candidate["model_group"] for candidate in body["rejected_candidates"]}
    assert rejected_groups == {"fast-small", "reasoning-strong", "fast-structured-output"}
    assert body["policy_id"]
    assert body["policy_version"]
    assert body["policy_digest"].startswith("sha256:")
    assert body["service_version"]
    assert body["environment"]


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
