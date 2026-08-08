# ADR 0014 — Enforce Governance runtime kill switch before replay consumption

Status: Accepted  
Date: 2026-08-08

## Context

P1.3 made Governance-signed Runtime Authorization mandatory at the Router boundary, P1.4 added
verifiable runtime violation evidence, and P1.5 continued W3C trace context. P1.6a added a durable
Governance runtime-control state machine and a monotonic Redis projection.

A short-lived authorization that was valid when issued must still become unusable immediately when
an operator activates the kill switch. Waiting for the authorization TTL is not an acceptable
containment mechanism. Restoring the agent must also not resurrect authorizations issued before the
transition.

The existing signed `subject.agent_version` already provides the authorization generation needed
for revocation, so adding another signed epoch claim would duplicate semantics.

## Decision

The Router consumes the P1.6a Redis snapshot keyed by signed `subject.agent_id`:

```json
{
  "schema_version": "1.0",
  "agent_id": "...",
  "control_epoch": 42,
  "state": "inactive",
  "revoked_through_agent_version": 17,
  "transition_id": "..."
}
```

Runtime control is checked only after the authorization has passed issuer/audience/time, key,
signature, request binding, agent binding, and Governance provenance checks, but **before** the
single-use replay identifier is consumed.

The order prevents invalid or forged artifacts from probing control state while ensuring a valid
pre-kill authorization returns the actual kill/revocation reason instead of being irreversibly
consumed first.

Stable denial codes are:

- `kill_switch_engaged` when the projected state is `active`;
- `runtime_authorization_revoked` when the signed `agent_version` is less than or equal to
  `revoked_through_agent_version`;
- `runtime_control_unavailable` when the snapshot is missing, malformed, incorrectly bound, uses an
  unsupported schema, or Redis cannot be trusted.

Active state takes precedence over the revocation floor. After deactivation, Governance retains the
floor, so old authorizations remain revoked while newly issued authorizations carry a higher signed
agent version.

## Deployment invariants

- Governance and Router must use the same Redis backend and exact key prefix.
- Redis snapshots have no TTL.
- Staging and production require Runtime Control to be explicitly enabled.
- Shared deployed Redis must use `rediss://`.
- The Router is read-only for the runtime-control namespace; a read-only Redis ACL is recommended.
- Governance P1.6a bootstrap/reconciliation must populate all governed agent snapshots before P1.6b
  enforcement is enabled.

## Consequences

No Runtime Authorization schema change is required. Existing P1.4 RuntimeViolation envelopes remain
compatible because the new reason codes map to the existing `authorization` category. W3C tracing
and `X-Correlation-Id` semantics are unchanged.
