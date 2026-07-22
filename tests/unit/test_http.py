"""Behavior tests for the ``POST /route`` HTTP entrypoint, using the shipped routing policy."""

import json
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

    with pytest.raises(RuntimeError, match="API_KEYS"), TestClient(app):
        pass


def test_startup_fails_closed_when_api_keys_is_malformed_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROUTING_POLICY_PATH", str(_SHIPPED_POLICY_PATH))
    monkeypatch.setenv("API_KEYS", "not valid json")

    with pytest.raises(RuntimeError, match="API_KEYS"), TestClient(app):
        pass


def test_startup_fails_closed_when_api_keys_is_not_an_object_of_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROUTING_POLICY_PATH", str(_SHIPPED_POLICY_PATH))
    monkeypatch.setenv("API_KEYS", json.dumps(["not", "an", "object"]))

    with pytest.raises(RuntimeError, match="API_KEYS"), TestClient(app):
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


def test_health_endpoint_requires_no_api_key(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_endpoint_requires_no_api_key(client: TestClient) -> None:
    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
