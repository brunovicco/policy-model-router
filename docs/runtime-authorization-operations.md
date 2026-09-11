# Runtime authorization and runtime control

A governed deployment can require that each `POST /route` request arrive inside a **signed runtime
scope** issued by an external Governance authority, and that an emergency stop be honored before
any decision is made.

Both are off by default and **mandatory in `staging` and `production`**: those environments refuse
to start with `RUNTIME_AUTHORIZATION_REQUIRED=false`, and runtime control cannot be enabled without
runtime authorization.

See [ADR-0012](adr/0012-signed-runtime-authorization.md),
[ADR-0013](adr/0013-structured-runtime-violation-evidence.md),
[ADR-0014](adr/0014-runtime-kill-switch-enforcement.md) and
[ADR-0016](adr/0016-w3c-runtime-trace-context.md).

## The request body

With enforcement on, the flat request is wrapped:

```json
{
  "request": { "schema_version": "1.0", "workload": "cashflow_analysis", "...": "..." },
  "authorization": {
    "protected": {
      "typ": "application/vnd.verifiable-ai-governance.runtime-authorization+json",
      "alg": "Ed25519",
      "kid": "governance-key-2026a"
    },
    "claims": {
      "authorization_id": "...",
      "issuer": "...",
      "audience": ["policy-model-router"],
      "issued_at": "...", "not_before": "...", "expires_at": "...",
      "subject": { "agent_id": "...", "agent_version": 3, "...": "..." },
      "request": {
        "workflow_id": "...", "task_id": "...", "workload": "...",
        "max_cost_usd_micros": 1000000, "...": "..."
      },
      "scope": {
        "risk_tier": "high",
        "data_classification": "restricted",
        "autonomy_level": "a2_prepare_for_approval",
        "models": [
          { "routing_group": "reasoning-strong", "allowed_data_classes": ["restricted"], "...": "..." }
        ],
        "kill_switch_enabled": true,
        "...": "..."
      },
      "scope_digest": "<sha256 hex>",
      "policy": { "policy_id": "...", "policy_digest": "<sha256 hex>", "...": "..." }
    },
    "signature": "<unpadded base64url of 64 Ed25519 bytes>"
  }
}
```

The flat `ModelRouteRequest` is accepted only while runtime authorization is explicitly disabled,
for local development and tests. Sending an authorization to a deployment that has it disabled is
itself a denial (`runtime_authorization_not_configured`) rather than a silently ignored field.

## Verification order

Each step fails closed with a bounded, machine-readable code.

1. **Identity and time** — issuer, audience, `issued_at`/`not_before`/`expires_at`, bounded to a
   ten-minute maximum lifetime.
2. **Key** — resolved by exact `kid` against a public-only trusted key set, with no fallback.
   Revoked keys and closed verification windows are rejected.
3. **Signature** — Ed25519 over canonical JSON of `{protected, claims}`, so the signing bytes stay
   byte-for-byte compatible with the issuing repository.
4. **Request binding** — eleven request facts must match the signed claims, so an authorization
   cannot be replayed against a different, cheaper or lower-risk request.
5. **Agent binding** — the calling `agent_name` must map to the signed Governance `agent_id`.
6. **Policy provenance** — the signed policy and control-catalog ids, versions and digests must be
   the ones this deployment trusts.
7. **Runtime control** — kill switch and revocation floor (below).
8. **Single use** — the `authorization_id` is consumed atomically; a replay is denied.
9. **Selected model** — *after* routing, the group this router selected must itself appear in the
   signed scope and be signed for the request's data classification.

Runtime control is checked after the artifact proves valid but **before** the single-use identifier
is consumed. The order keeps a forged artifact from probing control state, while a valid pre-kill
authorization still reports the real kill/revocation reason instead of being irreversibly spent
first.

## Runtime control

`RUNTIME_CONTROL_REQUIRED=true` makes the router read a Governance-owned, read-only Redis
projection keyed by the signed `agent_id`:

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

`state: "active"` means the kill switch is **engaged** and the request is denied; `"inactive"`
means execution may proceed. The polarity is the Governance projection's, mirrored here exactly
rather than reinterpreted.

The router denies when the kill switch is engaged, when the signed agent version is at or below
`revoked_through_agent_version`, or when the projection is missing or unreachable. **Absence of
state is a denial, not a default allow.**

## Denial evidence

A denial returns `403` with a content-minimized, digest-bound `violation` envelope:

```json
{
  "error": { "code": "selected_model_group_not_authorized", "message": "runtime authorization denied" },
  "violation": {
    "event": {
      "schema_version": "1.0", "event_id": "...", "occurred_at": "...",
      "source_service": "policy-model-router", "enforcement_action": "blocked",
      "category": "model_scope", "code": "selected_model_group_not_authorized",
      "correlation_id": "...",
      "authorization": { "state": "verified", "...": "..." },
      "request": { "workflow_id": "...", "task_id": "...", "agent_name": "...", "workload": "..." },
      "selected_model_group": "reasoning-strong"
    },
    "event_digest": "<sha256 hex>"
  }
}
```

Prompts, outputs, documents, request headers, API keys, provider credentials and exception messages
are excluded by construction. Authorization trust is reported coarsely as `absent`, `present` or
`verified`: a signature that happened to verify before a later binding failure stays `present`, so
the event never overstates trust.

Categories are `authorization`, `replay`, `request_binding`, `governance_provenance` and
`model_scope`.

### Denial codes

| Category | Codes |
|---|---|
| `authorization` | `runtime_authorization_required`, `runtime_authorization_not_configured`, `runtime_authorization_unavailable`, `invalid_signature`, `unknown_key`, `key_revoked`, `key_not_valid_for_issue_time`, `key_verification_window_closed`, `issuer_mismatch`, `audience_mismatch`, `issued_in_future`, `not_yet_valid`, `expired`, `invalid_time`, `kill_switch_engaged`, `runtime_authorization_revoked`, `runtime_control_unavailable` |
| `replay` | `replay_detected`, `replay_store_full`, `replay_store_unavailable` |
| `request_binding` | `request_binding_mismatch`, `agent_binding_mismatch`, `request_cost_precision_unsupported` |
| `governance_provenance` | `governance_policy_mismatch` |
| `model_scope` | `selected_model_group_not_authorized`, `selected_model_data_class_not_authorized` |

## Settings

| Variable | Default | Purpose |
|---|---|---|
| `RUNTIME_AUTHORIZATION_REQUIRED` | `false` | Master switch. Must be `true` in `staging`/`production`. |
| `RUNTIME_AUTHORIZATION_ISSUER` | `verifiable-ai-governance:production` | Trusted signer identity. |
| `RUNTIME_AUTHORIZATION_AUDIENCE` | `policy-model-router` | This service's audience value. |
| `RUNTIME_AUTHORIZATION_TRUSTED_KEY_SET_PATH` | *(unset)* | Public-only Ed25519 key set. Required when enforcement is on. |
| `RUNTIME_AUTHORIZATION_AGENT_BINDINGS_JSON` | `{}` | JSON object mapping `agent_name` to its Governance agent UUID. |
| `RUNTIME_AUTHORIZATION_EXPECTED_POLICY_ID` | `baseline-governance-policy` | Trusted Governance policy identity. |
| `RUNTIME_AUTHORIZATION_EXPECTED_POLICY_VERSION` | `1.0.0` | Trusted Governance policy version. |
| `RUNTIME_AUTHORIZATION_EXPECTED_POLICY_DIGEST` | *(unset)* | Lowercase SHA-256. Required when enforcement is on. |
| `RUNTIME_AUTHORIZATION_EXPECTED_CONTROL_CATALOG_ID` | `verifiable-ai-governance-baseline` | Trusted control catalog identity. |
| `RUNTIME_AUTHORIZATION_EXPECTED_CONTROL_CATALOG_VERSION` | `1.0.0` | Trusted control catalog version. |
| `RUNTIME_AUTHORIZATION_EXPECTED_CONTROL_CATALOG_DIGEST` | *(unset)* | Lowercase SHA-256. Required when enforcement is on. |
| `RUNTIME_AUTHORIZATION_CLOCK_SKEW_SECONDS` | `0` | Allowed skew, `0`–`60`. |
| `RUNTIME_AUTHORIZATION_MAX_KEY_SET_BYTES` | `262144` | Bound on the key-set file. |
| `RUNTIME_AUTHORIZATION_REPLAY_KEY_PREFIX` | `policy-model-router:runtime-auth:` | Redis namespace for replay consumption. |
| `RUNTIME_AUTHORIZATION_REPLAY_MAX_ENTRIES` | `10000` | In-memory replay guard bound. Local and test only — `REDIS_URL` is required in `staging`/`production`. |
| `RUNTIME_CONTROL_REQUIRED` | `false` | Enforce the kill switch and revocation floor. Must be `true` in `staging`/`production`. |
| `RUNTIME_CONTROL_REDIS_KEY_PREFIX` | `verifiable-ai-governance:runtime-control:v1:agent:` | Namespace shared with Governance. |
| `RUNTIME_CONTROL_MAX_SNAPSHOT_BYTES` | `4096` | Bound on one projection snapshot. |
| `RUNTIME_CONTROL_TIMEOUT_SECONDS` | `2.0` | Redis connect and read timeout for the projection. |

## Production example

```bash
export APP_ENV=production
export RUNTIME_AUTHORIZATION_REQUIRED=true
export RUNTIME_CONTROL_REQUIRED=true
export RUNTIME_AUTHORIZATION_ISSUER='verifiable-ai-governance:production'
export RUNTIME_AUTHORIZATION_AUDIENCE='policy-model-router'
export RUNTIME_AUTHORIZATION_TRUSTED_KEY_SET_PATH='/etc/policy-model-router/runtime-authorization-keys.json'
export RUNTIME_AUTHORIZATION_AGENT_BINDINGS_JSON='{"credit-analysis-agent":"<governance-agent-uuid>"}'
export RUNTIME_AUTHORIZATION_EXPECTED_POLICY_DIGEST='<Governance policy digest>'
export RUNTIME_AUTHORIZATION_EXPECTED_CONTROL_CATALOG_DIGEST='<Governance catalog digest>'
export REDIS_URL='rediss://redis:6379/0'
```

Redis is required for both the replay guard and the runtime-control projection in a deployed
environment, and runtime control additionally requires `rediss://`. Install the client with
`uv sync --extra rate-limit`.

## Rollout order

1. Deploy with both switches off and confirm normal routing.
2. Publish the trusted key set and the agent bindings; confirm the digests match Governance.
3. Turn on `RUNTIME_AUTHORIZATION_REQUIRED` in a non-production environment and watch
   `policy_model_router_runtime_authorization_total{outcome="denied"}` and
   `policy_model_router_runtime_violations_total`.
4. Turn on `RUNTIME_CONTROL_REQUIRED` once the Governance projection is publishing snapshots for
   every bound agent — a missing snapshot denies.
5. Promote to `staging`/`production`, where both are mandatory.

## Known gap: the gateway does not forward the envelope yet

[governed-llm-gateway](https://github.com/brunovicco/governed-llm-gateway) verifies this same
Governance contract for its own enforcement, but its Policy Decision Point adapter posts the flat
request body. A router deployment with `RUNTIME_AUTHORIZATION_REQUIRED=true` therefore answers it
`403 runtime_authorization_required`, and the gateway correctly fails closed before any provider
call. Until that forwarding lands, the supported composition is a non-enforcing router plus the
gateway's own governance enforcement. The gap is tracked on the gateway's roadmap.
