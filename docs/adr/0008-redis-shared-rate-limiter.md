# ADR-0008: Redis-backed rate limiter shared across replicas

- Status: Accepted
- Date: 2026-07-22

## Context

ADR-0007 shipped `InMemoryRateLimiter`: a fixed-window counter with no state shared across
replicas or worker processes. `docs/ARCHITECTURE.md`'s Known Gaps tracked this explicitly: a
multi-replica deployment enforces `RATE_LIMIT_MAX_REQUESTS` *per replica*, so a client can multiply
its effective quota by however many instances it can reach. Closing this needs a shared store; the
team decided a new infrastructure dependency was worth it now.

## Decision

**Optional, not required.** `REDIS_URL` is unset by default, and the service keeps working exactly
as before: `InMemoryRateLimiter`, per-process, no new dependency, no new deployment requirement.
Setting `REDIS_URL` switches `/route` to a cluster-wide limit; this requires the optional
`rate-limit` extra (`uv sync --extra rate-limit`, adding `redis>=5.0`) as a runtime dependency
mirroring the existing `tracing` extra's opt-in pattern (ADR: see `docs/LLM_OBSERVABILITY.md`).

**No network dependency unless explicitly requested.** `adapters/redis_rate_limiter.py` never
imports `redis` at module scope; `entrypoints/http.py::_build_rate_limiter` does the deferred
`from redis.asyncio import Redis` only when `REDIS_URL` is set, and raises `RuntimeError` (fails
the service closed) if the package isn't installed - a silent fallback to per-process behavior
would defeat the reason the operator configured it in the first place. `RedisRateLimiter` itself
takes a duck-typed `client: Any` needing only `incr`/`expire`/`ping`, so the class stays importable
and its own logic unit-testable without the optional package present at all.

**Fixed-window counter via `INCR` + `EXPIRE`.** `RedisRateLimiter.allow` increments
`policy-model-router:rate-limit:{key}` (atomic) and sets a TTL of `RATE_LIMIT_WINDOW_SECONDS` only
on the first increment of a window. Two concurrent "first" requests can each set the TTL, which
only ever shortens or matches the intended window - an accepted simplification of a fixed-window
counter, not a sliding-window or token-bucket algorithm. This mirrors `InMemoryRateLimiter`'s
existing fixed-window semantics exactly, so switching backends doesn't change the limiter's
behavior class, only its scope (per-process vs. cluster-wide).

**Fail-closed at startup, fail-open at runtime - different failure modes, different tolerance.**
`_lifespan` calls `rate_limiter.ping()` once at startup and refuses to start
(`RuntimeError`) if the configured Redis is unreachable, catching a bad `REDIS_URL` or a
misconfigured network immediately. `RedisRateLimiter.allow`, by contrast, fails open (returns
`True`, logs a warning) on *any* exception from the client at request time. This is deliberate: a
rate limiter is a defensive control on the request path, not this service's core value (routing
decisions never touch Redis); a transient Redis blip in production should not turn into a routing
outage. This mirrors the same principle already applied to the Langfuse tracing adapter
(`adapters/tracing.py`: "Telemetry must not turn a completed model call into a business failure"),
generalized here to "an anti-abuse control's outage must not turn a correct routing decision into
a failure."

**A uniform `RateLimiter.ping()` in the port.** `InMemoryRateLimiter.ping()` is a no-op (nothing to
check); `RedisRateLimiter.ping()` does a real connectivity check. `_lifespan` calls `ping()`
unconditionally on whichever limiter `_build_rate_limiter` returned, so the entrypoint never
branches on the concrete adapter type.

**Local development.** `docker-compose.yml` adds a single `redis:7-alpine` service so
`docker compose up -d redis` plus `REDIS_URL=redis://localhost:6379/0` reproduces the shared
behavior locally without any other infrastructure change.

## Consequences

- Single-instance and local-dev deployments are unaffected: no new dependency, no new
  configuration required, identical behavior to before this ADR.
- Multi-replica deployments that set `REDIS_URL` get a real, cluster-wide rate limit instead of a
  per-replica one - the gap this ADR closes.
- This adds Redis as a genuine infrastructure dependency for deployments that opt in: it must be
  provisioned, network-reachable from every replica, and monitored. A Redis outage does not take
  down routing (fail-open), but it does silently stop enforcing the limit until Redis recovers -
  that degradation is now both logged and counted; see the amendment below.
- `RateLimiter.allow`/`ping` becoming `async` changed `InMemoryRateLimiter`'s public method
  signatures too, even though it performs no I/O - a purely mechanical consequence of sharing one
  Protocol across a sync and an async-native implementation.

## Amendment (2026-07-22): real-Redis integration test and an alertable metric

This ADR originally shipped with two residual gaps, both flagged in `docs/ARCHITECTURE.md`'s Known
Gaps: no automated test against a real Redis, and no metric or alert on
`rate_limiter_backend_unavailable` - only a log line, so an extended outage would run the service
with no rate limit enforced and nobody would be paged. This amendment closes both, made the same
day once both were in scope.

**Real-Redis integration test.** `tests/integration/test_redis_rate_limiter_integration.py` runs
`RedisRateLimiter` against an actual `redis.asyncio.Redis` client: enforcing the limit, sharing one
counter across two separate limiter instances (the entire point of this ADR), and a successful
`ping()`. It skips itself - does not fail - when the optional `redis` package isn't installed or no
Redis is reachable at `REDIS_URL` (default `redis://localhost:6379/0`), so a plain `uv run pytest`
with no local infrastructure still passes; `tests/integration/conftest.py` supplies the
`anyio_backend` fixture these tests need. `.github/workflows/quality.yml` now runs a `redis:7-alpine`
service container and installs the `rate-limit` extra, so this module actually executes (not skips)
in CI on every push and pull request - a regression in the real `redis-py` client's behavior would
now be caught automatically, not just by the manual smoke test this ADR originally relied on.

**Prometheus counter, exposed on `GET /metrics`.** `adapters/redis_rate_limiter.py` defines
`policy_model_router_rate_limiter_backend_unavailable_total` (a `prometheus_client.Counter`),
incremented every time `RedisRateLimiter.allow` fails open. `entrypoints/http.py` adds
`GET /metrics`, unauthenticated and unthrottled like `/health`/`/readyz`, returning
`prometheus_client.generate_latest()`. `prometheus-client` is a required base dependency, not an
extra: unlike Redis or Langfuse, it needs no external system of its own to import or to serve
`/metrics` - whether anything actually scrapes that endpoint is an operational choice, not a code
dependency. A deployment can now alert on `increase(policy_model_router_rate_limiter_backend_unavailable_total[5m]) > 0`
in whatever Prometheus-compatible system scrapes it.

**Consequences of the amendment.** The metric only exists if something scrapes `/metrics`; this
ADR does not configure a Prometheus server, a ServiceMonitor, or an alert rule - those are
deployment-specific and out of this repository's scope. The counter is process-local like the
in-memory rate limiter's own state: each replica reports its own count, so a cluster-wide alert
needs a query that sums across instances (e.g. `sum(increase(...[5m])) > 0`), not a single
instance's value. No metric exists yet for the in-memory limiter or for `/route` outcomes more
broadly (allowed/rejected counts, latency); adding those is future work against a concrete need,
not speculative.
