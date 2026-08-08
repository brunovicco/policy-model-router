# P1.6b runtime kill-switch threat model

## Protected property

A runtime request must never be routed when Governance has activated the kill switch, when the
request carries an authorization generation that Governance has revoked, or when the Router cannot
establish a trusted runtime-control state.

## Threats and controls

| Threat | Control |
| --- | --- |
| Pre-kill valid token used after activation | Router reads current snapshot before replay consume and returns `kill_switch_engaged`. |
| Old token resurrects after restore | `revoked_through_agent_version` remains monotonic across restore; signed versions at/below the floor are rejected. |
| Forged token probes kill-switch state | Signature, request binding, agent binding, and Governance provenance are verified before runtime-control lookup. |
| Missing/evicted Redis key | Fail closed with `runtime_control_unavailable`; no implicit inactive default. |
| Malformed or oversized snapshot | Strict schema, field set, type, size, state, and agent-binding checks. |
| Redis timeout/outage | Fail closed with `runtime_control_unavailable`. |
| Snapshot for another agent substituted | Redis payload `agent_id` must equal the signed subject ID used in the key. |
| Unknown future schema silently accepted | Exact `schema_version == 1.0` and exact field set. |
| Replay hides the containment reason | Runtime control is checked before `replay_guard.consume()`. |
| Router mutates administrative state | Adapter exposes only GET/ping/close; operational ACL should be read-only. |
| Redis credential compromise | Use TLS, network isolation, least-privilege Redis ACLs, and separate Governance writer / Router reader credentials where supported. |
| Trace or logs become an alternate authority | Telemetry remains observational only; no trace field participates in enforcement. |

## Residual risks

Redis is a runtime projection, not the durable administrative source of truth. A malicious actor
with write access to the runtime-control namespace could forge a lower epoch or inactive snapshot
unless Redis ACLs and infrastructure controls prevent it. Governance P1.6a monotonic projection and
reconciliation protect normal stale-write/failure cases, while infrastructure authorization remains
part of the deployment trust boundary.
