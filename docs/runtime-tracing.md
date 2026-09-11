# Runtime tracing

The decision, and why trace context is observability metadata that can never influence an
authorization outcome, is [ADR-0016](adr/0016-w3c-runtime-trace-context.md).

Configure the Router through the existing `a2a-otel-kit` environment contract:

```env
A2A_OTEL_ENABLED=true
A2A_OTEL_OTLP_ENDPOINT=http://otel-collector:4318/v1/traces
A2A_OTEL_OTLP_TIMEOUT_SECONDS=5
```

`service_name`, `service_version`, `environment`, log level and log format are
set by the Router composition root.

The Router accepts only W3C `traceparent` and `tracestate` for propagation.
`X-Correlation-Id` remains the durable business/audit correlation identifier
and is never replaced by a trace ID.

## Why the version is pinned

`a2a-otel-kit==0.4.2` is pinned deliberately rather than tracked.
[`A2A_OTEL_KIT_UPGRADE_EVALUATION.md`](A2A_OTEL_KIT_UPGRADE_EVALUATION.md) records the review of a
later release: the import surface this Router uses is unchanged, so the upgrade is not blocked by
anything — it is simply held for a dedicated dependency PR that can regenerate the lock file and
isolate any telemetry behavior change on its own rollback path.
