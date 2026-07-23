# ADR-0007: HTTP boundary hardening - static API key, in-memory rate limit, health endpoints

- Status: Accepted
- Date: 2026-07-22

## Context

`entrypoints/http.py` exposed a single route, `POST /route`, with no authentication, no rate
limiting, and no endpoint an orchestrator could probe without exercising the routing use case
itself. The README documented this as an explicit deployment requirement ("deploy behind an
authenticated service-to-service gateway or mesh") rather than a control this service enforces.

This closes three related gaps at the HTTP boundary in one change, since they're all about what
happens to a request before it reaches `RouteModelUseCase`, not about routing behavior itself.

## Decision

**Authentication: a single static API key** (superseded - see the 2026-07-22 amendment below,
same-day). `entrypoints/http.py` requires the `API_KEY` environment variable at startup -
`_required_api_key()` raises `RuntimeError` if it's unset, failing the service closed the same way
a missing routing policy already does. `POST /route` depends on `require_api_key`, which compares
the `X-API-Key` request header against the configured key with `secrets.compare_digest`
(constant-time, to avoid a timing side-channel) and raises `AuthenticationError` (mapped to a 401
`unauthorized` envelope) on a missing or wrong header. This is one shared secret for the whole
service, not per-agent identity or scoped authorization - it proves "this caller is allowed to
reach the service," not "this caller is this specific agent."

**Rate limiting: in-memory, per (client IP, agent_name).** `adapters/rate_limiter.py`'s
`InMemoryRateLimiter` is a fixed-window counter, configured via `RATE_LIMIT_MAX_REQUESTS` /
`RATE_LIMIT_WINDOW_SECONDS` (defaults: 60 requests / 60 seconds), keyed by
`f"{client_ip}:{agent_name}"` inside the `POST /route` handler (after body parsing, since
`agent_name` lives in the request body, not a header). Exceeding the limit raises
`RateLimitExceededError`, mapped to a 429 `rate_limit_exceeded` envelope. State is a plain
in-process dict: safe under Uvicorn's single-threaded asyncio event loop without a lock, but **not
shared across replicas or worker processes** - each instance enforces its own limit independently.

**Health and readiness endpoints.** `GET /health` always returns `200 {"status": "ok"}` once the
process is serving - a liveness probe. `GET /readyz` returns `200 {"status": "ready"}` once the
lifespan has finished loading the routing policy - a readiness probe, but a shallow one. At the
time of this decision the service had no external dependency to check; ADR-0008 later added an
optional Redis dependency that is probed once at startup, not on each `/readyz` call. "Ready"
therefore means "startup completed," not "every runtime dependency is currently healthy." Neither
endpoint requires the API key or is rate-limited, so orchestrators and load balancers can probe
them cheaply and without credentials.

## Consequences

- `POST /route` callers must now send a valid `X-API-Key` header and stay within the configured
  rate limit; the Quick Start and API contract documentation were updated accordingly.
- The service fails to start without `API_KEY` configured - this is a deliberate deny-by-default
  choice, consistent with how a missing/invalid routing policy already stops startup, but it is a
  breaking operational change for any existing deployment that relied on the previous, unauthenticated
  behavior.
- The rate limiter bounds abuse from a single instance only. A multi-replica deployment that needs
  a cluster-wide limit must put a shared store (e.g. Redis) behind the same `RateLimiter`-shaped
  interface - today there is no port/protocol for it (unlike `AvailabilityProvider` in ADR-0006),
  because there is only one implementation and no second one to abstract over yet. Not resolved:
  the team decided this needs a new infrastructure dependency and deployment-topology change that
  shouldn't happen as a side effect of closing a documentation gap.
- `/readyz`'s shallow check means it cannot detect a policy that loaded successfully but is
  semantically wrong for the environment; it only detects a startup that didn't complete at all
  (which, in this single-process lifespan model, usually means the process isn't up to receive the
  probe either). Not resolved: deepening it requires a real external target to check, and per
  ADR-0004/ADR-0006 none exists yet.

## Amendment (2026-07-22): per-agent API keys replace the single shared secret

The original decision's own Consequences section flagged the single shared key as a known
limitation ("If per-agent identity or scoped authorization is needed, that is a new ADR"). This
amendment is that update, made the same day once a low-cost fix was in scope.

**Decision.** `API_KEY` (one string) is replaced by `API_KEYS`: a required environment variable
holding a JSON object mapping `agent_name` to that agent's own key, e.g.
`{"credit-analysis-agent": "...", "reporting-agent": "..."}`. `_required_api_keys()` parses and
validates it at startup - non-empty JSON object, non-empty string keys and values - failing closed
(`RuntimeError`) on anything else, same as before. The `require_api_key` dependency (which ran
before the request body was available) is replaced by `_authenticate(x_api_key, agent_name,
api_keys)`, called from inside the `POST /route` handler after `agent_name` is parsed from the
body: it looks up the configured key for that specific `agent_name` and rejects with the same
generic 401 whether the agent is unconfigured or the key is wrong, so the response never reveals
which agent names exist.

**Consequences.** One agent's key can now be rotated or revoked without affecting any other
agent's access - the debt this amendment closes. This still is not full IAM: there is no key
expiry, no scoping beyond "this agent may call `/route` as itself," and no audit trail of which key
was used beyond whatever the caller's `agent_name` claims (the service does not cross-check
`agent_name` against any identity assurance stronger than "knew the right key"). Compromising one
agent's key still lets an attacker call `/route` claiming to be that agent - mTLS or OAuth2 client
credentials would be needed to raise that bar, and remain a new ADR if ever required.
