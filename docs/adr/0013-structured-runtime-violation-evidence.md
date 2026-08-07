# ADR 0013 — Structured runtime violation evidence

Status: Accepted

## Context

P1.3 correctly blocks requests that fail signed Governance authorization, but the HTTP 403
response carries only a reason code. That is sufficient to deny execution but insufficient to
create durable cross-service evidence without relying on logs or parsing human-readable text.

## Decision

Every `RuntimeAuthorizationError` on `/route` returns a versioned `RuntimeViolationEnvelope` in
addition to the stable error object. The event is content-minimized and contains only:

- server-generated event ID and UTC time;
- Router service/environment identity;
- correlation ID;
- bounded violation category and reason code;
- workflow/task/agent/workload identifiers;
- authorization structural identifiers and digests when an envelope was present;
- selected logical model group only when routing reached that stage.

The event explicitly excludes prompts, outputs, documents, request headers, API keys, provider
credentials and exception messages. Its SHA-256 digest covers canonical JSON for tamper detection
when Governance consumes and persists it.

Authorization trust is deliberately coarse: `absent`, `present`, or `verified`. `verified` means
P1.3 verification completed successfully. A signature that happened to verify before a later
binding/provenance failure remains `present`; the violation event does not overstate trust.

Prometheus records bounded `category` and `code` labels, and the structured warning log contains
only the minimized event identifiers and digest.

## Consequences

Governance can distinguish a trustworthy Router denial from dependency failure and persist it as
routing evidence. A malformed, mismatched or digest-invalid violation response remains untrusted
and must fail closed upstream.

This ADR does not define incident severity, automatic incident creation, OpenTelemetry propagation,
or kill-switch activation. Those belong to later governance layers.
