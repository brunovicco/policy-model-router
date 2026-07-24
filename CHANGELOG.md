# Changelog

All notable changes to this project are documented in this file. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

A `v0.1.0` git tag exists from early Docker/GHCR-publishing scaffolding work, predating the
feature set below; it has no corresponding GitHub Release and was never treated as a real release
of this service. `0.2.0` is the first version that reflects what the service actually does.

## Unreleased

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
