# Runtime kill-switch enforcement

P1.6b makes the Policy Model Router an enforcement consumer of the runtime-control projection owned
by `verifiable-ai-governance` P1.6a.

The decision is [ADR-0014](adr/0014-runtime-kill-switch-enforcement.md). What this control is meant
to stop, and which threats it deliberately does not address, is
[`runtime-kill-switch-threat-model.md`](runtime-kill-switch-threat-model.md) — read it before
changing any of the fail-closed behavior below, because each rule here answers a threat there.

## Configuration

The Router reuses `REDIS_URL` and reads the exact Governance namespace:

```env
RUNTIME_CONTROL_REQUIRED=true
RUNTIME_CONTROL_REDIS_KEY_PREFIX=verifiable-ai-governance:runtime-control:v1:agent:
RUNTIME_CONTROL_MAX_SNAPSHOT_BYTES=4096
RUNTIME_CONTROL_TIMEOUT_SECONDS=2
REDIS_URL=rediss://runtime-control-redis.example:6379/0
```

Local development may keep `RUNTIME_CONTROL_REQUIRED=false`. Staging and production fail startup
unless it is true. When enforcement is required, `RUNTIME_AUTHORIZATION_REQUIRED` must also be true.

## Rollout order

1. Deploy and migrate Governance P1.6a.
2. Configure Governance against the shared Redis runtime-control backend.
3. Run the Governance bootstrap/reconciliation command so every governed agent has a snapshot.
4. Confirm the Router can reach the same Redis backend and namespace.
5. Prefer a Redis credential with read-only access to
   `verifiable-ai-governance:runtime-control:v1:agent:*` for the Router.
6. Enable `RUNTIME_CONTROL_REQUIRED=true` on the Router.
7. Run the P1.6 end-to-end kill/restore/revocation scenario.

Do not enable Router-side fail-closed enforcement before the Governance bootstrap is complete:
missing snapshots are intentionally treated as `runtime_control_unavailable`.

## Expected behavior

For two valid pre-kill authorizations A and B:

1. A routes normally and is consumed by replay protection.
2. Governance activates the kill switch.
3. Previously issued but unconsumed B is denied with `kill_switch_engaged`.
4. Governance persists the returned P1.4 RuntimeViolation evidence.
5. Governance deactivates the kill switch while retaining the revocation floor.
6. An old authorization at or below that floor is denied with `runtime_authorization_revoked`.
7. A newly issued authorization with a higher signed `agent_version` may route normally.

Redis absence, timeout, malformed JSON, unsupported schema, mismatched agent ID, unknown fields, or
oversized snapshots fail closed as `runtime_control_unavailable`.
