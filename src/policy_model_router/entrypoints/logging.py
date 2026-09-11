"""Per-request logging context.

Process-wide logging is configured by ``a2a-otel-kit``'s ``Observability.configure()`` in
``entrypoints/http.py``'s lifespan; this module owns only the per-request binding on top of it.
Never log secrets, personal data, prompts, or model responses - see
``.claude/rules/security-privacy.md``.
"""

import structlog


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
    ``Observability.configure()`` at startup, silently dropping them from every
    log line for the rest of the process.
    """
    structlog.contextvars.unbind_contextvars("correlation_id", "trace_id")
