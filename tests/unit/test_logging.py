"""Tests for the structured logging bootstrap."""

import json
import logging
from collections.abc import Iterator

import pytest

from policy_model_router.entrypoints.logging import (
    bind_correlation_id,
    clear_request_context,
    configure_logging,
)


@pytest.fixture(autouse=True)
def _reset_logging() -> Iterator[None]:
    """Reset stdlib logging handlers before and after each test."""
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    yield
    root.handlers = original_handlers


def test_configure_logging_emits_json_with_service_fields_when_json_format(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A configured logger writes one JSON line with the bound service fields."""
    configure_logging(service="billing", environment="test", version="1.2.3", json_format=True)

    logging.getLogger(__name__).info("order_created")

    output = capsys.readouterr().out.strip()
    payload = json.loads(output)
    assert payload["service"] == "billing"
    assert payload["environment"] == "test"
    assert payload["version"] == "1.2.3"
    assert payload["event"] == "order_created"


def test_configure_logging_emits_non_json_console_output_when_json_format_is_false(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``json_format=False`` selects the human-readable console renderer, not JSON."""
    configure_logging(service="billing", environment="test", version="1.2.3", json_format=False)

    logging.getLogger(__name__).info("order_created")

    output = capsys.readouterr().out.strip()
    with pytest.raises(json.JSONDecodeError):
        json.loads(output)
    assert "order_created" in output


def test_configure_logging_respects_log_level(capsys: pytest.CaptureFixture[str]) -> None:
    """A DEBUG record is dropped when level is INFO."""
    configure_logging(service="billing", environment="test", version="1.2.3", level="INFO")

    logging.getLogger(__name__).debug("noisy_detail")

    assert capsys.readouterr().out.strip() == ""


def test_configure_logging_defaults_to_info_and_json(capsys: pytest.CaptureFixture[str]) -> None:
    """Omitting ``level``/``json_format`` keeps the documented INFO/JSON defaults."""
    configure_logging(service="billing", environment="test", version="1.2.3")

    logging.getLogger(__name__).debug("dropped_by_default_level")
    logging.getLogger(__name__).info("kept_by_default_level")

    output = capsys.readouterr().out.strip()
    payload = json.loads(output)
    assert payload["event"] == "kept_by_default_level"


def test_clear_request_context_keeps_process_wide_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Clearing per-request context drops correlation IDs but keeps service fields."""
    configure_logging(service="billing", environment="test", version="1.2.3")
    bind_correlation_id("req-1", trace_id="trace-1")

    clear_request_context()
    logging.getLogger(__name__).info("after_clear")

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["service"] == "billing"
    assert "correlation_id" not in payload
    assert "trace_id" not in payload
