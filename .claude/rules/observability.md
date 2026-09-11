---
paths:
  - "src/**/*.py"
---

# Logging and observability rules

- Emit structured logs with stable event names.
- Prefer keyword fields over interpolated prose.
- Include service, environment, version, outcome, duration, correlation ID, and trace ID when applicable.
- Use UTC timestamps.
- Log exceptions once at the boundary that handles them.
- Redact by allowlist; never dump arbitrary objects or payloads.
- Separate logs, metrics, traces, and immutable audit events by purpose.
- Add metrics for latency, throughput, errors, retries, circuit state, queue lag, and business outcomes where relevant.
- Propagate W3C trace context across HTTP and messaging boundaries.
- `print()` is prohibited in production code.
- Configure process-wide logging once, in the FastAPI lifespan, through `a2a-otel-kit`'s `Observability.configure()`; never configure logging elsewhere.
- Bind and clear per-request context through `entrypoints/logging.py`'s `bind_correlation_id()`/`clear_request_context()`.
