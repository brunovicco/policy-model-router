"""Behavior tests for the ``POST /route`` HTTP entrypoint, using the shipped routing policy."""

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from policy_model_router.entrypoints import http as http_module
from policy_model_router.entrypoints.http import app

_SHIPPED_POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "routing_policy.yaml"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient]:
    """A TestClient wired to the real shipped routing policy, triggering FastAPI's lifespan."""
    monkeypatch.setenv("ROUTING_POLICY_PATH", str(_SHIPPED_POLICY_PATH))
    with TestClient(app) as test_client:
        yield test_client


def _valid_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": "1.0",
        "requested_at": "2026-07-19T12:00:00Z",
        "workflow_id": "workflow-1",
        "task_id": "task-1",
        "agent_name": "financeiro-agent",
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

    with TestClient(app):
        pass

    assert len(calls) == 1
    assert calls[0]["service"] == "policy-model-router"
    assert calls[0]["environment"]
    assert calls[0]["version"]


def test_route_returns_the_decision_for_a_valid_request(client: TestClient) -> None:
    response = client.post("/route", json=_valid_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["selected_model_group"] == "reasoning-medium"
    assert body["workflow_id"] == "workflow-1"
    assert body["task_id"] == "task-1"
    rejected_groups = {candidate["model_group"] for candidate in body["rejected_candidates"]}
    assert rejected_groups == {"fast-small", "reasoning-strong", "fast-structured-output"}


def test_route_returns_a_stable_error_envelope_for_an_invalid_request(client: TestClient) -> None:
    response = client.post("/route", json=_valid_payload(workload="not_a_real_workload"))

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
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "no_viable_model_group"
    assert "fast-small" in body["error"]["message"]
