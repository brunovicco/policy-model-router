# Changelog

All notable changes to this project are documented in this file. Format based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). No version has been tagged yet; entries
so far are grouped under Unreleased.

## Unreleased

### Added

- Deterministic, policy-based `/route` endpoint implementing ADR-0005's two-step routing
  algorithm, with a declarative `config/routing_policy.yaml` policy and fail-closed loader.
- Per-agent API key authentication and a two-tier (`per client IP` and `per (client IP, agent)`)
  rate limiter, in-memory by default and optionally shared across replicas via Redis (ADR-0007,
  ADR-0008).
- `GET /health`, `GET /readyz`, and `GET /metrics` (Prometheus format), including `/route`
  outcome/duration metrics and rate-limiter admit/block and backend-failure counters.
- Decision provenance: every routing decision carries `policy_id`/`policy_version`/
  `policy_digest`/`service_version`/`environment` (ADR-0009), and each rejected candidate carries
  a machine-readable `reason_code`/`observed_value`/`required_value` alongside the existing
  human-readable `reason`.
- Token-based cost estimation: model-group cost is priced per input/output token instead of one
  flat number per group (ADR-0010).
- Correlation ID propagation: every request is bound to an `X-Correlation-Id` (reused from the
  caller or generated) for the duration of its handling, echoed back on the response.
- Typed, validated runtime configuration (`entrypoints/settings.py`), replacing ad-hoc
  `os.environ.get()` calls and manual numeric casts.
- Multi-stage, non-root `Dockerfile` and a GitHub Actions workflow publishing versioned images to
  GitHub Container Registry on SemVer tags.
- `LICENSE` (MIT), `SECURITY.md`, `CONTRIBUTING.md`, and `.github/CODEOWNERS`.

### Fixed

- The published Docker image now ships the `redis` client (`--extra rate-limit`) so `REDIS_URL`
  works in the built artifact, not only in local development.
- Rate limiting runs before authentication, so repeated invalid-API-key attempts are throttled
  instead of bypassing the limiter entirely.
- The Redis-backed rate limiter's `INCR`/`EXPIRE` pair is now a single atomic Lua script with
  self-healing for a key found without a TTL, closing a window where a crash between the two
  commands could leave a key rate-limited forever.
- The Redis-backed limiter's fail-open log fingerprint is HMAC-keyed instead of an unkeyed hash,
  so an attacker with only log access cannot enumerate and match the low-entropy
  `(IP, agent_name)` key space against it.
- Both rate limiters' backend connections are released on graceful shutdown instead of being
  dropped.
- `schema_version` in the routing policy file is now validated against the exact supported value
  instead of accepted as an arbitrary string.
- A `RATE_LIMIT_WINDOW_SECONDS` value under one second no longer silently disables the Redis-backed
  limiter: the atomic Lua script now sets the key's TTL in milliseconds (`PEXPIRE`) instead of
  whole seconds (`EXPIRE`), which previously truncated any sub-second window to `0` - a TTL Redis
  treats as "delete immediately," resetting the counter on every request.
- Releasing one rate-limit tier's Redis connection on shutdown no longer skips releasing the
  other's if the first `close()` raises.
- `docs/DEVELOPMENT.md`'s `docker run`/`uvicorn` examples now include the required `API_KEYS`,
  matching `README.md`'s.

### Security

- `GET /docs`, `/redoc`, and `/openapi.json` are disabled by default (`ENABLE_API_DOCS=true` to
  opt in for local development).
- Removed the unused, Langfuse-based LLM call tracing adapter: this service never calls an LLM, so
  there was nothing for it to trace.
- Fixed an information-disclosure regression introduced alongside the structured `reason_code`
  work above: a restricted model group's rejection no longer includes the names of the other
  agents allowlisted for it. That candidate's rejection reaches every authenticated caller via
  `rejected_candidates` on an otherwise-successful `/route` response, not just the requesting
  agent, so it must never reveal other agents' identities - the same guarantee `_authenticate`
  already makes on the auth-failure path.
