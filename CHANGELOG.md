# Changelog

All notable changes to this project are documented in this file. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

A `v0.1.0` git tag exists from early Docker/GHCR-publishing scaffolding work, predating the
feature set below; it has no corresponding GitHub Release and was never treated as a real release
of this service. `0.2.0` is the first version that reflects what the service actually does.

## Unreleased

## [0.5.0] - 2026-09-11

This release turns the service from a deterministic router into a runtime policy enforcement
point. Everything below is opt-in and off by default; `staging` and `production` refuse to start
without it.

### Added

- Signed Governance runtime authorization on `POST /route` (ADR-0012). The governed request body
  wraps the existing `ModelRouteRequest` alongside an Ed25519-signed authorization envelope, and is
  verified in a fixed order: issuer and audience, validity window, exact trusted `kid` and its
  lifecycle, signature over canonical protected header plus claims, request binding, agent binding,
  and Governance policy provenance. The authorization is single-use: its identifier is consumed
  atomically, through Redis where configured, so a replay is denied. After routing, the selected
  model group must itself appear in the signed scope and be signed for the request's data
  classification. Configured through `RUNTIME_AUTHORIZATION_*`.
- Structured runtime violation evidence (ADR-0013). Every runtime authorization denial returns a
  versioned, content-minimized `violation` envelope alongside the stable error object, carrying a
  bounded category and reason code, structural identifiers, and a SHA-256 digest over canonical
  JSON for tamper detection. Authorization trust is reported coarsely as `absent`, `present`, or
  `verified`, so a signature that verified before a later binding failure is never overstated as
  trusted. Prompts, outputs, documents, headers, credentials and exception messages are excluded by
  construction.
- Runtime kill-switch and revocation-floor enforcement (ADR-0014). The Router reads a
  Governance-owned, read-only Redis projection keyed by the signed `agent_id` and denies when the
  kill switch is engaged, when the signed agent version is at or below the revocation floor, or
  when the projection is missing or unreachable - absence of state is a denial, not a default
  allow. Checked after the authorization proves valid but *before* the single-use identifier is
  consumed, so a valid pre-kill authorization reports the real reason instead of being irreversibly
  spent. Configured through `RUNTIME_CONTROL_*`.
- W3C trace context continuation at the routing boundary (ADR-0016), via
  `a2a-otel-kit`. Incoming `traceparent`/`tracestate` are extracted into a SERVER span with
  content-free attributes, alongside the existing bounded `X-Correlation-Id`. The trace context is
  observability metadata only: it never participates in authorization, replay detection, policy
  evaluation, model selection, or violation integrity.
- `policy_model_router_runtime_authorization_total` and
  `policy_model_router_runtime_violations_total` on `GET /metrics`, both with bounded labels.

### Changed

- Workload and logical model-group names are validated policy-defined identifiers rather than
  closed Python enums (ADR-0015). They are 1-128 lowercase characters from `a-z0-9._-`, beginning
  and ending alphanumeric, and are recognized only once the active routing policy declares them.
  New workload identifiers must be namespace-qualified (`rag.answer`); the five credit-desk names
  remain valid unqualified through the 0.x compatibility window. A syntactically valid workload the
  policy does not declare is no longer rejected by the transport schema - it reaches the policy
  boundary and fails closed there. `Workload` and `ModelGroup` remain importable as aliases of
  `WorkloadId` and `ModelGroupId`. See `docs/MIGRATION_TO_GENERIC_POLICY.md`.
- The routing policy loader rejects model groups that no workload can select, in addition to
  workloads referencing undeclared groups.
- Both READMEs now document the runtime enforcement boundary, its settings, the `403` violation
  envelope, and this service's role as the Policy Decision Point for `governed-llm-gateway`, whose
  enforcement point may only narrow what this router authorizes. `docs/ARCHITECTURE.md` lists the
  modules its layer tree had omitted and tracks two previously unrecorded gaps.

### Fixed

- The `workload` label on the three route metrics is bounded to the vocabulary the active policy
  declares; every undeclared workload is reported as `undeclared`. Since ADR-0015 made workloads
  caller-supplied identifiers, the previous behavior let any authenticated caller create unbounded
  Prometheus child metrics that are never reclaimed - and the `finally` block recorded them even
  for a workload that failed closed. Structured logs keep the verbatim value, and the previously
  silent undeclared-workload path now emits a `routing_decision` log line.

### Known gaps

- The runtime enforcement modules sit at the package root, outside the layer structure, and
  `scripts/validate_architecture.py` does not inspect files outside a layer - so the gate reports
  success without checking them. Two of them import `entrypoints.contracts`, inverting the
  dependency rule. Tracked in `docs/ARCHITECTURE.md`.
- `governed-llm-gateway` does not yet produce signed runtime authorization, so a Router deployment
  with `RUNTIME_AUTHORIZATION_REQUIRED=true` returns `403` to it and the gateway fails closed before
  any provider call. That integration is pending on the Governance side.

## [0.4.0] - 2026-07-23

### Changed

- Re-vendored `engineering-loop-schemas` from `0.1.2` to `0.3.0`
  (commit `5340d491b46f4fabc967c81bb3e5204104b6b5d8`) under
  `scripts/_vendor_loop_schemas/`. Evidence and verdict wire formats advance to
  `2.0.0` (breaking); the bundle now ships the stdlib structural evaluator
  (`_stdlib_jsonschema.py`), the installed-schema loader (`schema_resources.py`),
  and the four canonical JSON Schemas.
- `scripts/validate_loop_schema_vendor.py` now enforces manifest version `2.0.0`,
  the expanded required-file set (including `schemas/*.json`), every declared
  package-import adaptation, and provenance headers on all vendored Python
  sources.

### Security

- `validate()` in the vendored bundle now enforces the canonical contract JSON
  Schema instead of relying on dataclass construction, closing acceptance gaps
  for unknown properties, invalid enums, wrong types, and empty or duplicate
  collections.

## [0.3.0] - 2026-07-23

### Added

- Full policy and deployment provenance for rejected routing decisions, matching accepted decisions and preserving a machine-readable rejection reason.
- Pre-parse per-IP rate limiting, `Content-Length` body-size enforcement, and bounds for caller-supplied identifiers and token estimates (ADR-0011).
- Docker build and runtime smoke tests in pull-request validation.
- SPDX SBOM and build-provenance attestations for published container images.

### Fixed

- Context-window validation now accounts for estimated input and output tokens together.
- Runtime settings reject invalid environments, log levels, log formats, non-finite values, and rate-limit windows outside the operational range `(0, 86,400]` seconds.
- The Redis rate limiter validates and converts its window during construction, preventing an extreme value from raising `OverflowError` on the request path.
- Routing-policy loading rejects duplicate YAML keys instead of silently keeping the last value.
- Release validation runs the Redis integration suite with the optional dependency installed.
- GitHub Actions are pinned to immutable commit SHAs.
- Policy-digest and readiness documentation now match the implemented normalized-text digest and optional Redis startup dependency.

### Security

- Request abuse is limited before FastAPI parses malformed or oversized bodies.
- Structured routing-decision events no longer record caller-supplied `workflow_id` or `task_id`; correlation remains available through `routing_decision_id` and `correlation_id`.

## [0.2.0] - 2026-07-23

### Added

- Deterministic, policy-based `/route` endpoint implementing ADR-0005's two-step routing algorithm, with a declarative `config/routing_policy.yaml` policy and fail-closed loader.
- Per-agent API key authentication and a two-tier (`per client IP` and `per (client IP, agent)`) rate limiter, in-memory by default and optionally shared across replicas via Redis (ADR-0007, ADR-0008).
- `GET /health`, `GET /readyz`, and `GET /metrics` (Prometheus format), including `/route` outcome/duration metrics and rate-limiter admit/block and backend-failure counters.
- Decision provenance: every routing decision carries `policy_id`/`policy_version`/`policy_digest`/`service_version`/`environment` (ADR-0009), and each rejected candidate carries a machine-readable `reason_code`/`observed_value`/`required_value` alongside the existing human-readable `reason`.
- Token-based cost estimation: model-group cost is priced per input/output token instead of one flat number per group (ADR-0010).
- Correlation ID propagation: every request is bound to an `X-Correlation-Id` (reused from the caller or generated) for the duration of its handling, echoed back on the response.
- Typed, validated runtime configuration (`entrypoints/settings.py`), replacing ad-hoc `os.environ.get()` calls and manual numeric casts.
- Multi-stage, non-root `Dockerfile` and a GitHub Actions workflow publishing versioned images to GitHub Container Registry on SemVer tags.
- `LICENSE` (MIT), `SECURITY.md`, `CONTRIBUTING.md`, and `.github/CODEOWNERS`.

### Fixed

- The published Docker image now ships the `redis` client (`--extra rate-limit`) so `REDIS_URL` works in the built artifact, not only in local development.
- Rate limiting runs before authentication, so repeated invalid-API-key attempts are throttled instead of bypassing the limiter entirely.
- The Redis-backed rate limiter's `INCR`/`EXPIRE` pair is now a single atomic Lua script with self-healing for a key found without a TTL, closing a window where a crash between the two commands could leave a key rate-limited forever.
- The Redis-backed limiter's fail-open log fingerprint is HMAC-keyed instead of an unkeyed hash, so an attacker with only log access cannot enumerate and match the low-entropy `(IP, agent_name)` key space against it.
- Both rate limiters' backend connections are released on graceful shutdown instead of being dropped.
- `schema_version` in the routing policy file is now validated against the exact supported value instead of accepted as an arbitrary string.
- A `RATE_LIMIT_WINDOW_SECONDS` value under one second no longer silently disables the Redis-backed limiter: the atomic Lua script now sets the key's TTL in milliseconds (`PEXPIRE`) instead of whole seconds (`EXPIRE`), which previously truncated any sub-second window to `0` - a TTL Redis treats as "delete immediately," resetting the counter on every request.
- Releasing one rate-limit tier's Redis connection on shutdown no longer skips releasing the other's if the first `close()` raises.
- `docs/DEVELOPMENT.md`'s `docker run`/`uvicorn` examples now include the required `API_KEYS`, matching `README.md`'s.

### Security

- `GET /docs`, `/redoc`, and `/openapi.json` are disabled by default (`ENABLE_API_DOCS=true` to opt in for local development).
- Removed the unused, Langfuse-based LLM call tracing adapter: this service never calls an LLM, so there was nothing for it to trace.
- Fixed an information-disclosure regression introduced alongside the structured `reason_code` work above: a restricted model group's rejection no longer includes the names of the other agents allowlisted for it. That candidate's rejection reaches every authenticated caller via `rejected_candidates` on an otherwise-successful `/route` response, not just the requesting agent, so it must never reveal other agents' identities - the same guarantee `_authenticate` already makes on the auth-failure path.
