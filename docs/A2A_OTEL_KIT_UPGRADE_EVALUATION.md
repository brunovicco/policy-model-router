# a2a-otel-kit dependency evaluation for Phase 1

Date: 2026-08-31

The router currently pins `a2a-otel-kit==0.4.2`. The current `a2a-otel-kit` repository version was
reviewed at `0.6.0` as part of Phase 1 planning.

The public imports used by this router remain present in `0.6.0`, including:

- `Observability`
- `ObservabilitySettings`
- `continue_trace`

The newer kit also remains compatible with Python 3.13 and preserves the vendor-neutral
OpenTelemetry/W3C propagation boundary used by the router.

## Decision

Do not mix the dependency upgrade into the generic-policy contract change.

The Phase 1 vocabulary migration changes core authorization semantics and should have a focused diff
and independent rollback path. Upgrade `a2a-otel-kit` in a dedicated follow-up PR that can regenerate
`uv.lock`, run the full observability/integration suite, and isolate any telemetry behavior change.

## Status

- compatibility review: no import-surface blocker found;
- upgrade: deferred to a dedicated dependency PR;
- Phase 1 generic-policy work: not blocked by the current `0.4.2` pin.
