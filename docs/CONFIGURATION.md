# Configuration

Every setting is read from the environment and validated at startup: a malformed value fails the
service closed with a clear error rather than surfacing later as an opaque cast failure.

Runtime authorization and runtime control have their own settings, documented in
[`runtime-authorization-operations.md`](runtime-authorization-operations.md).

## Core

| Variable | Default | Purpose |
| --- | --- | --- |
| `API_KEYS` | *(required)* | JSON object mapping each `agent_name` to its own API key, checked against `X-API-Key` on `POST /route`. The service refuses to start if unset, empty or malformed. |
| `ROUTING_POLICY_PATH` | `config/routing_policy.yaml` | Path to the active routing policy. |
| `APP_ENV` | `development` | `development`, `staging`, `production` or `test`. `staging` and `production` additionally require runtime authorization and runtime control. |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL`. |
| `LOG_FORMAT` | `json` | `console` for a human-readable renderer during local development. |
| `ENABLE_API_DOCS` | `false` | Serves `/docs`, `/redoc` and `/openapi.json`. Off by default: a minor recon surface that orchestrators never need. Read at import time — see the note below. |

## Rate limiting

Both tiers are checked before authentication, so repeated invalid-key attempts are throttled too.
See [ADR-0007](adr/0007-http-boundary-hardening.md) and
[ADR-0008](adr/0008-redis-shared-rate-limiter.md).

| Variable | Default | Purpose |
| --- | --- | --- |
| `RATE_LIMIT_MAX_REQUESTS` | `60` | Requests per `(client IP, agent_name)` pair per window. |
| `RATE_LIMIT_PER_IP_MAX_REQUESTS` | `600` | Requests per client IP alone per window, checked first and before authentication. |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Window length in seconds, shared by both tiers. Greater than `0`, at most `86400`. |
| `RATE_LIMIT_MAX_TRACKED_KEYS` | `100000` | In-memory limiter only, ignored once `REDIS_URL` is set. Caps distinct keys per tier, evicting the least-recently-touched. |
| `RATE_LIMIT_FINGERPRINT_SECRET` | *(unset)* | Redis-backed limiter only. HMAC key for the fail-open log fingerprint. Unset uses a random per-process secret: stable within a process, not across restarts. |
| `REDIS_URL` | *(unset)* | Shares both tiers across replicas. Requires `uv sync --extra rate-limit`. Unset keeps the per-process limiter. |
| `MAX_REQUEST_BODY_BYTES` | `16384` | Maximum `POST /route` body, checked against `Content-Length` before parsing. A chunked body with no `Content-Length` is not checked ([ADR-0011](adr/0011-http-boundary-pre-parse-limits.md)). |

The rate-limit key's IP component is always the raw TCP peer address: this service never reads
`X-Forwarded-For` or `Forwarded` itself. Behind a reverse proxy every real client shares the
proxy's IP, collapsing per-client granularity into one bucket. To recover it, configure the proxy
to pass a trusted header and scope Uvicorn to that specific hop — the container image exposes this
as `TRUSTED_PROXY_IPS`. Never trust forwarded headers from an unrestricted set of peers, or any
client can forge the header and multiply its quota.

The Redis-backed limiter fails **open** on a backend error — a rate-limiter outage must not become
a routing outage — but fails the service **closed** at startup if the configured Redis is
unreachable. Every fail-open increments
`policy_model_router_rate_limiter_backend_unavailable_total`; alert on a sustained increase, which
means the configured limit is not being enforced.

## Container

| Variable | Default | Purpose |
|---|---|---|
| `TRUSTED_PROXY_IPS` | *(unset)* | When set, the image starts Uvicorn with `--proxy-headers --forwarded-allow-ips`. Set it to the proxy's own address; never `*`. |

## A note on `ENABLE_API_DOCS`

Unlike every other setting, this one is read at import time rather than in the lifespan. That is a
FastAPI constraint, not an oversight: `docs_url`, `redoc_url` and `openapi_url` are consumed by
`FastAPI.__init__`, which registers or omits the documentation routes there and then — reassigning
the attributes later moves nothing. The practical consequence is narrow, since a served process has
its environment set before the module is imported. It shows up only under a harness that
re-triggers the lifespan with a different environment, where the flag keeps the value it had at
import.
