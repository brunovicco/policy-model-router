# Policy Model Router

**English** | [Português (Brasil)](README.pt-BR.md)

[![quality](https://github.com/brunovicco/policy-model-router/actions/workflows/quality.yml/badge.svg)](https://github.com/brunovicco/policy-model-router/actions/workflows/quality.yml)
[![Python 3.13](https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![ghcr.io](https://img.shields.io/badge/ghcr.io-policy--model--router-2496ED?logo=docker&logoColor=white)](https://github.com/brunovicco/policy-model-router/pkgs/container/policy-model-router)

> A fail-closed policy decision point that decides **which class of model may serve an LLM
> workload** — before inference, and outside agent prompts and application code.

A caller declares a workload, its risk tier, its data classification and its operational limits.
`POST /route` evaluates that against a versioned policy and answers with an explainable decision
record — or an explicit rejection carrying the same provenance. It never calls a model, never sees
a prompt, and holds no provider credential.

```text
Agent / application
        │
        │ workload + risk tier + data classification + limits
        ▼
Policy Model Router  (PDP)  ← this repository
        ├─ closed contract validation
        ├─ signed Governance authorization + kill switch   (optional)
        ├─ workload → model-group lookup
        ├─ ordered eliminatory constraints
        └─ decision record with policy + deployment provenance
        │
        │ selected_model_group, and why every other group was not
        ▼
Governed LLM Gateway  (PEP)
        ▼
LLM providers
```

```text
Gateway allowed set ⊆ Policy Router authorized set
```

The enforcement point may narrow what this router authorized. It may never widen it.

[One governed decision](#one-governed-decision) · [What it gives you](#what-it-gives-you) · [How it works](#how-it-works) · [Running it](#running-it) · [The policy file](#the-policy-file) · [API contract](#api-contract) · [Governed deployments](#governed-deployments) · [Observability](#observability) · [Repository map](#repository-map) · [Scope](#scope) · [Where to read next](#where-to-read-next)

## One governed decision

Restricted data and a 100,000-token context. Only the workload's mapped group survives, and the
response says exactly why each of the others did not.

```bash
curl -X POST http://127.0.0.1:8000/route \
  -H 'Content-Type: application/json' -H 'X-API-Key: dev-local-key' \
  -d '{
    "schema_version": "1.0", "requested_at": "2026-07-22T12:00:00Z",
    "workflow_id": "credit-review-42", "task_id": "correlate-findings-7",
    "agent_name": "credit-analysis-agent", "workload": "findings_correlation",
    "risk_level": "high", "data_classification": "restricted",
    "context_tokens_estimated": 100000, "max_output_tokens_estimated": 2000,
    "structured_output_required": false, "max_latency_ms": 60000, "max_cost_usd": 1.00
  }'
```

```json
{
  "schema_version": "1.0",
  "routing_decision_id": "674088f4-cd75-45e9-a6b5-5e85b8cc5588",
  "decided_at": "2026-07-22T12:00:01Z",
  "selected_model_group": "reasoning-strong",
  "reason": "workload 'findings_correlation' maps to model group 'reasoning-strong' and satisfies all constraints",
  "rejected_candidates": [
    {
      "model_group": "fast-small",
      "reason_code": "data_classification_not_authorized",
      "observed_value": "restricted",
      "required_value": "public, internal"
    },
    {
      "model_group": "reasoning-medium",
      "reason_code": "context_window_exceeded",
      "observed_value": "102000",
      "required_value": "<= 64000"
    }
  ],
  "policy_id": "credit-desk-routing",
  "policy_version": "1.0.0",
  "policy_digest": "sha256:2f1a...c9",
  "service_version": "0.5.0",
  "environment": "production"
}
```

Every rejection carries a machine-readable `reason_code` with the `observed_value` and the
`required_value`, so an audit trail or a UI never has to parse prose. `policy_digest` is a SHA-256
of the loaded policy's content, so a decision names the exact policy that produced it even when
nobody remembered to bump `policy_version`.

**A denial is as auditable as an approval.** When the mapped group fails a constraint the router
answers `422` and does not quietly promote the request to a stronger group — and that rejection
carries the same five provenance fields an acceptance does. The enforcement point downstream has to
be able to prove *which* policy denied a call, not merely that something did.

## What it gives you

| Capability | What it means in practice |
| --- | --- |
| **Model choice out of prompts** | An agent declares what it needs, not which model it wants. Changing the mapping is a policy edit, not a prompt edit in every agent. |
| **Deterministic and reproducible** | The same request against the same policy yields the same group and the same rejection reasons. No scoring, no sampling, no tie-breaks. |
| **Explainable by construction** | Every non-selected group appears in the decision with the constraint that eliminated it. |
| **Fail-closed everywhere** | An invalid policy, an unknown workload, a missing runtime-control snapshot, an unverifiable signature: each denies rather than degrades. |
| **Provenance on every outcome** | Policy id, version and digest, plus service version and environment — on rejections as well as decisions. |
| **Governed runtime scope** | Optionally require an Ed25519-signed, single-use authorization bound to the exact request, plus an emergency stop. |

## How it works

For each request the use case looks up the model group the workload maps to, evaluates **every**
declared group against the ordered constraints below — stopping at that candidate's first failure —
and selects the mapped group only if it survived. Every other group is reported as rejected, either
for the constraint it failed or because the workload maps elsewhere.

Evaluating the groups that cannot be selected is deliberate: it is what makes the decision
explainable. It is audit cost, not routing cost — and a caller that will not persist the
explanation can decline it with `"include_rejected_candidates": false`, paying for one candidate
instead of the whole catalog without changing the decision.

Order matters, because the first constraint a candidate fails becomes its rejection reason.

| # | Constraint | The candidate is rejected when |
|---:|---|---|
| 1 | Data classification | The group is not authorized for the request's classification |
| 2 | Risk level | The group is not authorized for the workflow's risk tier |
| 3 | Structured output | The request requires it and the group does not support it |
| 4 | Tool calling | The workload requires it and the group does not support it |
| 5 | Context window | Estimated input + expected output together exceed the group's limit |
| 6 | Cost ceiling | Estimated token-priced cost exceeds `max_cost_usd` |
| 7 | Latency ceiling | Typical group latency exceeds `max_latency_ms` |
| 8 | Availability | The availability provider resolves the group as unavailable |
| 9 | Agent allowlist | The group is restricted and the requesting agent is not listed |

The predicates are pure functions in [`domain/constraints.py`](src/policy_model_router/domain/constraints.py);
the two-step algorithm is in [`application/route_model.py`](src/policy_model_router/application/route_model.py).

## Running it

Requirements: Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/brunovicco/policy-model-router.git
cd policy-model-router
uv sync --frozen
export API_KEYS='{"credit-analysis-agent":"dev-local-key"}'   # required, keyed by agent_name
uv run uvicorn policy_model_router.entrypoints.http:app --reload
```

The service listens on `http://127.0.0.1:8000` and loads `config/routing_policy.yaml` at startup.

### Container

```bash
docker run --rm -p 8000:8000 \
  -e API_KEYS='{"credit-analysis-agent":"dev-local-key"}' \
  ghcr.io/brunovicco/policy-model-router:0.5
```

The image is non-root and multi-stage, declares a `HEALTHCHECK` against `/health`, and honors
`TRUSTED_PROXY_IPS` — unset by default, so the rate-limit key uses the raw TCP peer address and no
forwarded header is trusted from anyone. Set it to the proxy's own address behind an ingress; never
to `*`, or any client can forge the header and multiply its quota.

## The policy file

[`config/routing_policy.yaml`](config/routing_policy.yaml) holds workload mappings and model-group
capabilities. The loader demands complete coverage, rejects unknown fields, and refuses to start on
anything malformed. It also rejects a group no workload maps to, so configuration cannot be left
behind by accident — declare `staged: true` on a canary or reserve you are provisioning
deliberately ahead of its workload.

The shipped example declares five workloads across four logical groups. Values are policy inputs
maintained by the policy author, not live provider measurements.

| Model group | Authorized data | Authorized risk | Context | Typical latency | USD / M tokens (in / out) |
|---|---|---|---:|---:|---:|
| `fast-small` | public, internal | low, medium | 16k | 3,000 ms | 0.10 / 0.40 |
| `reasoning-medium` | + confidential, restricted | + high | 64k | 15,000 ms | 0.50 / 1.50 |
| `reasoning-strong` | + confidential, restricted | + critical | 128k | 30,000 ms | 2.00 / 8.00 |
| `fast-structured-output` | public, internal | low, medium | 8k | 2,000 ms | 0.10 / 0.40 |

Authorized risk is a decision-quality rule, not a data-protection one: a group can be cleared for
the data involved and still be unauthorized for a high-stakes decision.

**Reloading.** Send `SIGHUP` to re-read the policy without restarting:

```bash
docker kill --signal=HUP <container>      # or: kubectl exec <pod> -- kill -HUP 1
```

The swap is atomic — a request resolves the policy once and keeps it for its lifetime, so the
`policy_digest` a decision reports is the one that actually decided it. If the new file fails to
load, the running policy is kept and the service keeps serving: refusing would turn a YAML typo
into an outage. Alert on `policy_model_router_policy_reloads_total{outcome="failed"}` rather than
assuming a reload worked. Under multiple workers, each holds its own policy and needs its own
signal.

## API contract

`POST /route` accepts a closed schema: unknown fields are rejected, timestamps must be
timezone-aware UTC, and numeric limits must be positive.

| Field | Accepted values |
|---|---|
| `schema_version` | Exactly `1.0` |
| `requested_at` | UTC timestamp |
| `workflow_id`, `task_id`, `agent_name` | Non-empty, at most 200 characters |
| `workload` | Any policy-defined identifier: 1–128 lowercase `a-z0-9._-`, alphanumeric at both ends. New workloads are namespace-qualified (`rag.answer`). A valid identifier the policy does not declare is not rejected by the schema — it reaches the policy boundary and fails closed there ([ADR-0015](docs/adr/0015-policy-defined-workload-and-model-group-identifiers.md), [migration guide](docs/MIGRATION_TO_GENERIC_POLICY.md)) |
| `risk_level` | `low`, `medium`, `high`, `critical` |
| `data_classification` | `public`, `internal`, `confidential`, `restricted` |
| `context_tokens_estimated`, `max_output_tokens_estimated` | Integer, 0 to 10,000,000 |
| `structured_output_required` | Boolean |
| `max_latency_ms` | Positive integer |
| `max_cost_usd` | Positive decimal |
| `include_rejected_candidates` | Boolean, default `true`. Set `false` to evaluate only the mapped group: same selection, same accept/reject outcome, `rejected_candidates` returned empty rather than omitted |

Both token estimates feed the cost constraint: a group is priced per token, input and output
separately, so estimated cost is a function of the call's actual size ([ADR-0010](docs/adr/0010-token-based-cost-estimation.md)).

| Status | Code | Meaning |
|---:|---|---|
| 401 | `unauthorized` | Missing or invalid `X-API-Key` |
| 403 | *(bounded runtime denial code)* | Runtime authorization or runtime control denied it; the body carries a `violation` envelope |
| 413 | `payload_too_large` | Body exceeds `MAX_REQUEST_BODY_BYTES` per its declared `Content-Length` |
| 422 | `invalid_request` | The request does not match the contract |
| 422 | `no_viable_model_group` | The mapped group failed a hard constraint; the body carries the full rejected decision |
| 429 | `rate_limit_exceeded` | Too many requests for this `(client IP, agent_name)` pair |
| 500 | `misconfigured_routing_policy` | The active policy declares no rule for the requested workload |

**Authentication** is a per-agent API key matched against the key configured for the request's own
`agent_name`, in constant time. An unknown agent and a wrong key return the identical error, so the
response never reveals which agents are configured. This is not full IAM — no expiry, no scoping,
no assurance beyond "knew the right key" ([ADR-0007](docs/adr/0007-http-boundary-hardening.md)).

**Rate limiting** runs on two tiers, both *before* authentication so invalid-key attempts are
throttled too: a per-IP tier in ASGI middleware ahead of body parsing, then a per-`(IP, agent_name)`
tier. The first exists so a caller cannot dodge the second by varying `agent_name`. Both are
per-process by default; set `REDIS_URL` to share them across replicas
([ADR-0008](docs/adr/0008-redis-shared-rate-limiter.md)).

Settings are listed in [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md).

## Governed deployments

Everything above is the router's own policy boundary. A governed deployment can additionally
require that each request arrive inside a **signed runtime scope** issued by an external Governance
authority, and that an emergency stop be honored before any decision is made. Both are off by
default and mandatory in `staging`/`production` — those environments refuse to start without them.

With enforcement on, `POST /route` takes `{"request": ..., "authorization": ...}`, and the envelope
is verified in a fixed order before routing and re-checked after it:

1. **Identity and time** — issuer, audience, validity window, bounded to a ten-minute lifetime.
2. **Key** — resolved by exact `kid` against a public-only trust set, no fallback.
3. **Signature** — Ed25519 over canonical JSON, byte-compatible with the issuing repository.
4. **Request binding** — eleven request facts must match the signed claims.
5. **Agent binding** — the calling `agent_name` must map to the signed Governance `agent_id`.
6. **Policy provenance** — the signed policy and control-catalog identity must be the trusted one.
7. **Runtime control** — kill switch and revocation floor, read from a Governance projection
   ([enforcement](docs/runtime-kill-switch-enforcement.md),
   [threat model](docs/runtime-kill-switch-threat-model.md)).
8. **Single use** — the authorization identifier is consumed atomically; a replay is denied.
9. **Selected model** — *after* routing, the selected group must itself be in the signed scope.

Each step fails closed with a bounded code, and a denial returns `403` with a content-minimized,
digest-bound `violation` envelope: category, code, structural identifiers, and nothing else. No
prompts, headers, credentials or request content, by construction.

Full setup, the envelope shape, every denial code and all nineteen settings are in
[`docs/runtime-authorization-operations.md`](docs/runtime-authorization-operations.md).

## Observability

`GET /health` and `GET /readyz` are liveness and startup probes; `GET /metrics` is Prometheus text.
All three are unauthenticated and unthrottled by design, so restrict them at the ingress.

| Metric | Labels |
|---|---|
| `policy_model_router_route_decisions_total` | `workload`, `model_group` |
| `policy_model_router_route_rejections_total` | `workload`, `outcome` |
| `policy_model_router_route_duration_seconds` | `workload` |
| `policy_model_router_rate_limit_decisions_total` | `tier`, `outcome` |
| `policy_model_router_rate_limiter_backend_unavailable_total` | — |
| `policy_model_router_runtime_authorization_total` | `outcome` |
| `policy_model_router_runtime_violations_total` | `category`, `code` |
| `policy_model_router_policy_reloads_total` | `outcome` |

The `workload` label carries the requested workload only when the active policy declares it;
anything else is reported as `undeclared`, since workloads are caller-supplied identifiers and an
unbounded label is unbounded memory. Structured logs keep the verbatim value.

Every decision also emits a `routing_decision` log line with the decision id, correlation id,
workload, model group, reason code and policy identity. Caller-supplied `workflow_id` and `task_id`
stay out of logs per [`docs/PRIVACY.md`](docs/PRIVACY.md). Incoming W3C trace context is continued
across the boundary ([ADR-0016](docs/adr/0016-w3c-runtime-trace-context.md),
[configuration](docs/runtime-tracing.md)).

## Repository map

| Path | What lives there |
|---|---|
| `src/policy_model_router/domain/` | Controlled vocabularies, policy value objects, pure constraint predicates, trusted key set, kill-switch state |
| `src/policy_model_router/application/` | The routing use case, the authorization verifier, the control enforcer, and their ports |
| `src/policy_model_router/adapters/` | YAML policy loader, clock, IDs, availability, rate limiters, replay guards, projection stores |
| `src/policy_model_router/entrypoints/` | Pydantic wire contracts, the FastAPI app, settings, error mapping, violation evidence |
| `config/`, `examples/policies/` | The shipped policy and alternative example policies |
| `docs/adr/` | Fifteen accepted decisions, amended rather than rewritten |
| `scripts/` | The project quality gate and its architecture, MCP and contract validators |

Dependencies point inward only — `entrypoints → application → domain`, `adapters → application/domain`,
and `domain` depends on no outer layer. The gate fails on any module that sits outside a layer, so
the rule is enforced rather than documented.

## Scope

This service deliberately does not:

- choose a provider, deployment or credential, or call a model;
- run a live provider health check — availability is resolved through a port, but the only shipped
  implementation passes the policy's static flag through;
- score or rank viable alternatives, or fall back when the mapped group is rejected;
- provide full IAM at the transport edge;
- watch the policy file or reload it on its own;
- share rate-limit state across replicas without opting into Redis.

Those boundaries keep policy decisions explicit. Tracked gaps are listed in
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md#known-gaps).

## Where to read next

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — layers, dependency rules, diagrams, known gaps
- [`docs/adr/`](docs/adr/) — why each boundary looks the way it does
- [`docs/runtime-authorization-operations.md`](docs/runtime-authorization-operations.md) — governed deployments, end to end
- [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) — every environment variable
- [`docs/MIGRATION_TO_GENERIC_POLICY.md`](docs/MIGRATION_TO_GENERIC_POLICY.md) — policy-defined identifiers
- [`docs/runtime-kill-switch-enforcement.md`](docs/runtime-kill-switch-enforcement.md) and its [threat model](docs/runtime-kill-switch-threat-model.md) — stopping a governed agent mid-flight
- [`docs/runtime-tracing.md`](docs/runtime-tracing.md) — continuing a distributed trace, and the [dependency evaluation](docs/A2A_OTEL_KIT_UPGRADE_EVALUATION.md) behind the pinned version
- [`docs/PRIVACY.md`](docs/PRIVACY.md) — what never reaches a log
- [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) and [`AGENTS.md`](AGENTS.md) — working on this repository

```bash
uv run python scripts/quality_gate.py        # lint, format, types, tests, security, audit, packaging
```

## License

[MIT](LICENSE).
