# ADR 0012 - Require signed Governance authorization at the runtime Router boundary

## Status

Accepted.

## Date

2026-08-07.

## Context

The Router already authenticates callers with per-agent API keys and applies deterministic hard
constraints. Those controls do not prove that Governance approved the specific agent/model/request
scope being presented now.

Governance P1.1/P1.2 defines and signs a short-lived Ed25519 authorization envelope. P1.3 makes the
Router an enforcing consumer of that proof.

## Decision

`POST /route` accepts the existing flat `ModelRouteRequest` only when runtime authorization is
explicitly disabled for local/test compatibility. The governed form is:

```json
{
  "request": { "...": "ModelRouteRequest v1" },
  "authorization": { "...": "signed Governance envelope v1" }
}
```

In staging and production, startup fails unless `RUNTIME_AUTHORIZATION_REQUIRED=true`.

The Router verifies, in order:

1. strict P1.1 contract shape and Ed25519-only protected metadata;
2. exact issuer and intended audience;
3. issue/not-before/expiry window;
4. exact trusted `kid`, key lifecycle and key verification window;
5. Ed25519 signature over canonical protected header + claims;
6. exact route-request binding, including `requested_at`, workflow/task/workload and budgets;
7. exact agent-name to signed Governance agent UUID deployment binding;
8. exact Governance policy and control-catalog provenance;
9. atomic single-use consumption of `authorization_id`;
10. after deterministic routing, selected logical model group must exist in the signed model
    allowlist and authorize the signed data classification.

## Replay

Production replay protection is a Redis `SET key 1 NX EX ttl` operation. Redis failure is
fail-closed for runtime authorization even though the separate rate-limiter subsystem may have its
own availability policy.

Local/test deployments may use the bounded in-memory replay guard.

## Key distribution

The Router receives public Ed25519 JWKs only. Private signing material never enters this repository
or service.

The trusted key set is loaded at process startup. A normal rotation or emergency revocation is
activated at the Router by deploying/restarting with the new key-set generation.

## Dependency

The Router declares `cryptography` directly for Ed25519 verification. This is not JWT/JWS; the
library is used only for the raw Ed25519 primitive over Governance canonical bytes.

## Consequences

- API key compromise alone is insufficient to authorize a different Governance agent/scope.
- Request tampering and cross-task replay fail before deterministic policy evaluation.
- A Router policy mapping cannot silently escape the signed Governance model allowlist.
- Horizontal production deployments require Redis replay state.
- Stable denial codes become inputs to P1.4 violation evidence.
