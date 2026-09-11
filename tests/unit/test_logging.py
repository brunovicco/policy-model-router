"""Tests for the per-request logging context helpers.

Process-wide logging is configured by ``a2a-otel-kit`` in the FastAPI lifespan, so these tests set
up structlog directly rather than through this package.
"""

import json

import pytest
import structlog

from policy_model_router.entrypoints.logging import bind_correlation_id, clear_request_context


@pytest.fixture(autouse=True)
def _structlog_to_stdout() -> None:
    """Render structlog events as JSON on stdout, the way the configured process does."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )


def test_clear_request_context_keeps_process_wide_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Clearing per-request context drops correlation IDs but keeps the startup-bound fields."""
    structlog.contextvars.bind_contextvars(service="billing", environment="test", version="1.2.3")
    bind_correlation_id("req-1", trace_id="trace-1")

    clear_request_context()
    structlog.get_logger(__name__).info("after_clear")

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["service"] == "billing"
    assert payload["environment"] == "test"
    assert "correlation_id" not in payload
    assert "trace_id" not in payload


def test_bind_correlation_id_without_a_trace_id_binds_only_the_correlation_id(
    capsys: pytest.CaptureFixture[str],
) -> None:
    bind_correlation_id("req-2")

    structlog.get_logger(__name__).info("bound")

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["correlation_id"] == "req-2"
    assert "trace_id" not in payload
