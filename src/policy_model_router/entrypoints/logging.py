"""Structured logging bootstrap.

Call :func:`configure_logging` once, at process startup, before any other code
emits a log line. Renders JSON to stdout by default; pass ``json_format=False``
for a human-readable renderer during local development. Never log secrets,
personal data, prompts, or model responses - see
``.claude/rules/security-privacy.md``.
"""

import logging
import sys

import structlog


def configure_logging(
    *, service: str, environment: str, version: str, level: str = "INFO", json_format: bool = True
) -> None:
    """Configure structlog and standard-library logging for this process.

    ``level`` and ``json_format`` are the caller's already-validated configuration (see
    ``entrypoints/settings.py::Settings``), not read from the environment here - this module has
    no env var reads of its own.
    """
    resolved_level = getattr(logging, level.upper(), logging.INFO)

    shared_processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer = (
        structlog.processors.JSONRenderer() if json_format else structlog.dev.ConsoleRenderer()
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(resolved_level)

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        service=service, environment=environment, version=version
    )


def bind_correlation_id(correlation_id: str, *, trace_id: str | None = None) -> None:
    """Bind correlation and trace identifiers to the current logging context."""
    fields = {"correlation_id": correlation_id}
    if trace_id is not None:
        fields["trace_id"] = trace_id
    structlog.contextvars.bind_contextvars(**fields)


def clear_request_context() -> None:
    """Clear per-request context variables without dropping process-wide fields.

    Removes only ``correlation_id`` and ``trace_id``. Using
    :func:`structlog.contextvars.clear_contextvars` here would also drop the
    ``service``/``environment``/``version`` fields bound once at startup by
    :func:`configure_logging`, silently dropping them from every log line for
    the rest of the process.
    """
    structlog.contextvars.unbind_contextvars("correlation_id", "trace_id")
