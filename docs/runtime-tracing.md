# Runtime tracing

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
