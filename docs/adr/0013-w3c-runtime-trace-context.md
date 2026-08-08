# ADR 0013 — Continue W3C trace context at the routing enforcement boundary

Status: Accepted  
Date: 2026-08-07

## Context

P1.3 verifies signed runtime authorization and P1.4 returns digest-bound
violation evidence. The Router already receives the Governance routing-decision
ID through `X-Correlation-Id`, but cross-service operational traces were not
continued.

`a2a-otel-kit` 0.4.2 already provides stateless W3C propagation, safe
attribute sanitization, OTLP/HTTP export, and trace-aware structured logging.

## Decision

Adopt `a2a-otel-kit==0.4.2` at the Router HTTP boundary.

For each request:

1. extract `traceparent`/`tracestate`;
2. attach the extracted context;
3. create a SERVER span with content-free attributes only;
4. keep the existing bounded `X-Correlation-Id`;
5. execute authentication, authorization and deterministic routing unchanged;
6. emit existing P1.4 violation logs while the span is active;
7. detach the context after the response.

The W3C context is observability metadata only. It never participates in
authorization, replay detection, policy evaluation, model selection, or
violation integrity.

## Privacy and failure semantics

No prompts, outputs, authorizations, API keys, request bodies, arbitrary
headers, customer data, or remote exception messages are recorded.

Tracing is disabled by default. Export failures are best effort and cannot
turn an allowed request into a denial or a denial into an allow.

The P1.4 RuntimeViolationEvent schema remains unchanged. Its digest-bound
`correlation_id` is the durable bridge back to the distributed trace.
