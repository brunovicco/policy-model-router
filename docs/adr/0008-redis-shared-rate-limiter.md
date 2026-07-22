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

## Amendment (2026-07-22, second): security review follow-ups

`/security-review` on the merged PR confirmed the design above and raised four items; two were
fixed here, two are documentation-only because fixing them in code would mean guessing at
deployment topology this repository doesn't control.

**Fixed: the rate-limit key (which embeds the caller's IP) was logged in plain text on fail-open.**
`RedisRateLimiter.allow`'s `except` branch logged `key=key` - the full `f"{client_ip}:{agent_name}"`
string built in `entrypoints/http.py` - violating `.claude/rules/security-privacy.md` ("Redact by
allowlist; never dump arbitrary objects or payloads") and leaving no entry in `docs/PRIVACY.md` for
an IP address now durably stored by whatever log system ingests this service's stdout. Fixed by
logging `key_fingerprint` - `hashlib.sha256(key).hexdigest()[:12]` - instead: an operator can still
tell whether repeated failures come from the same key or many different ones, but cannot recover
the IP from the log. `tests/unit/test_redis_rate_limiter.py::test_allow_never_logs_the_raw_key_on_failure`
asserts the raw key never appears in the captured log event.

**Fixed: `InMemoryRateLimiter._windows` had no eviction, so it grew without bound.** This predates
ADR-0008 (it's ADR-0007's original limiter) but the review's proxy-spoofing hypothesis below made
the memory-growth angle concrete enough to close now rather than leave open indefinitely. The dict
became an `OrderedDict` capped at `RATE_LIMIT_MAX_TRACKED_KEYS` (default 100,000; configurable, and
ignored once `REDIS_URL` is set - Redis manages its own memory via the `EXPIRE` on each key), moving
a key to the end on every touch and evicting the least-recently-touched key once the cap is
exceeded. An attacker who could vary the observed key without bound (see the next item) can no
longer grow this process's memory without bound in step.

**Documented, not coded: `/metrics` should be network-restricted to internal scrapers in
production.** Like `/health` and `/readyz`, it is deliberately unauthenticated and unthrottled at
the application layer so orchestrators/scrapers can reach it cheaply; this repository does not
configure an ingress or network boundary for any of the three, and none exists in
`Dockerfile`/`docker-compose.yml`. Operators should restrict `/metrics` (and, more loosely,
`/health`/`/readyz`) to internal networks at the ingress/mesh layer, the same way `/route` is
already documented as needing an authenticated gateway in front of it. No code change: the
right boundary is a deployment concern, not something this service can enforce on itself.

**Documented, not coded: the rate-limit key trusts only the raw TCP peer address, never a proxy
header.** `entrypoints/http.py`'s `rate_limit_key` uses `http_request.client.host` directly; Uvicorn
is not configured with `--proxy-headers`/`--forwarded-allow-ips`, and no `ProxyHeadersMiddleware` is
installed. Today this means: behind a reverse proxy, every real client's requests carry the proxy's
own IP as the key's IP component, collapsing per-client granularity to one shared bucket per proxy
- not attacker-spoofable as things stand, but also not doing what the `(client IP, agent_name)`
granularity promises in that topology. The hypothesis raised in review - that enabling proxy-header
trust *without* restricting it to a specific trusted hop would let any client forge
`X-Forwarded-For` and multiply its effective quota - is accurate but describes a future
misconfiguration, not current behavior; guessing at a specific deployment's proxy topology and
coding for it here would be speculative. Documented instead: a deployment that sits behind a
reverse proxy and wants real per-client granularity must configure the proxy to pass a trusted
header and configure Uvicorn/Starlette to trust only that specific hop (e.g. `--forwarded-allow-ips`
scoped to the proxy's own address) - never trust forwarded headers from an unrestricted set of
peers.
