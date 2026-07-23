# ADR-0011: Pre-parse body-size cap and relocated per-IP rate limit

- Status: Accepted
- Date: 2026-07-23

## Context

`docs/ARCHITECTURE.md`'s Known Gaps tracked this explicitly: FastAPI fully parses and
Pydantic-validates `ModelRouteRequest` before the `/route` handler body runs, so a malformed or
oversized JSON body raises `RequestValidationError` (422) before either the per-IP or per-agent
rate-limit tier - both checked inside the handler - ever runs. A caller flooding `/route` with bad
bodies from one IP bypasses both tiers entirely, at the cost of only a JSON parse and a Pydantic
validation per request, no routing work, no credential comparison. The same gap note observed that
fixing it "means moving the per-IP check into ASGI middleware (the per-agent tier cannot move
there as-is - it needs `request.agent_name` from the parsed body) - real design work, deferred to
its own ADR." This is that ADR.

There was also no request body size cap anywhere - no uvicorn flag, no ASGI middleware - and the
installed Starlette/FastAPI/uvicorn versions ship no built-in mechanism for one (checked directly
against the installed package sources: no size-limit middleware module in Starlette, no bound on
`Request.body()`/`Request.stream()`, no uvicorn flag for it). `workflow_id`/`task_id`/`agent_name`
had no `max_length`, and `context_tokens_estimated`/`max_output_tokens_estimated` had no upper
bound.

## Decision

**A pure ASGI middleware, not `BaseHTTPMiddleware`.** `entrypoints/http.py` gains
`_BodySizeAndIpRateLimitMiddleware`, registered via `app.add_middleware(...)` - not the
`@app.middleware("http")` decorator style `_bind_correlation_id` already uses. That style is sugar
for `BaseHTTPMiddleware`, which already buffers the request body via `call_next` before any
downstream code runs, defeating a pre-parse check. The new middleware wraps the raw ASGI
`(scope, receive, send)` callable directly, so both its checks run before any body bytes are read.

**The per-IP rate-limit tier moves here, replacing the in-handler check.** The existing
`ip_rate_limiter.allow(f"ip:{client_host}")` call (previously inside `route()`, after body parsing)
now runs in this middleware, for `POST /route` only - `/health`/`/readyz`/`/metrics` stay
unthrottled, matching ADR-0007's design intent. It is relocated, not duplicated: the in-handler
call is removed, so a request consumes the IP-tier budget exactly once, in exactly one place. The
same key format, same `RATE_LIMIT_DECISIONS_TOTAL` metric, and the same 429 `rate_limit_exceeded`
envelope are preserved - this changes *where* the check runs, not its behavior or shape. Traced
through the new call order, the three existing rate-limiter tests
(`test_route_is_rate_limited_per_client_and_agent`, `test_invalid_api_key_attempts_are_rate_limited`,
`test_route_is_rate_limited_per_client_ip_even_when_agent_name_varies`) produce identical status
codes and needed no changes - confirmed by running them, not just by inspection.

The per-agent tier stays exactly where it is - inside `route()`, before authentication - because it
needs `agent_name` from the parsed body and cannot run any earlier; this is unchanged from
ADR-0007/ADR-0008 and is not itself a bug.

**Order, for `POST /route`: IP rate limit, then body-size check.** A flood of oversized bodies must
still exhaust the IP budget rather than get a free pass by being rejected before the limiter ever
sees it - so the IP-tier check runs first, and the body-size check runs second (for every path, not
just `/route`).

**Body-size enforcement is a `Content-Length` header check only - a deliberate scope decision.**
`entrypoints/settings.py` gains `max_request_body_bytes` (default 16 KiB, following the same
`Field(..., gt=0)` pattern as the existing rate-limit numeric settings), stored on
`app.state.max_request_body_bytes` during `_lifespan` (read fresh per request, the same pattern
already used for `ip_rate_limiter`/`rate_limiter`) rather than baked into the middleware's
constructor at import time - the latter would freeze the value at first module import, before a
test harness (or any process that reconfigures the environment and re-triggers the lifespan without
re-importing the module) could ever change it. If `Content-Length` declares a body over the cap,
the middleware responds `413 payload_too_large` without invoking the downstream app. A request
using chunked transfer-encoding with no `Content-Length` header is **not** caught by this check -
an accepted residual gap, not an oversight: this service is documented (ADR-0004) as deployed
behind an authenticated gateway, not exposed directly, and closing this gap fully would mean
wrapping `receive()` to count actual bytes as they stream in, aborting mid-stream if the running
total exceeds the cap - real additional complexity (receive-wrapping, the edge case of aborting
after the downstream app may have already started sending a response) that wasn't judged
proportionate to this service's actual threat model for this round.

**Bounded identifiers and token ceilings**, in `entrypoints/contracts.py`:
`ModelRouteRequest.workflow_id`/`.task_id`/`.agent_name` gain `max_length=200` via a new
`_BoundedIdentifier` type (kept separate from `_NonEmptyStr`, which stays unbounded - it's shared by
many response-side fields, like `policy_digest`/`reason`, where a length cap doesn't make sense).
`context_tokens_estimated`/`max_output_tokens_estimated` gain an upper bound of 10,000,000 - generous
relative to the shipped policy's largest `max_context_tokens` (128,000), but ruling out an absurd
value with no legitimate use that only pollutes downstream cost/context-window arithmetic.

**A caller-supplied `X-Correlation-Id` over 200 characters is ignored, not rejected.**
`_bind_correlation_id` now treats an oversized header value the same as a missing one - generating
a fresh UUID instead - rather than failing the request: this header is a caller convenience, not a
security control, so an oversized value should only cost that one caller its own correlation, not a
hard failure.

## Consequences

- The exact gap this ADR closes - a malformed/oversized body bypassing rate limiting entirely - is
  fixed for the IP tier specifically. The agent tier still cannot move earlier (it needs the parsed
  body), so a flood that varies `agent_name` per attempt (already closed by the per-IP tier since
  ADR-0008's third amendment) remains closed the same way it already was; this ADR does not change
  that story, only where the IP-tier enforcement point sits.
- `413 payload_too_large` is a new stable error code, alongside the existing `401`/`422`/`429`/`500`
  ones.
- `workflow_id`/`task_id`/`agent_name` over 200 characters, or token estimates over 10,000,000, are
  now rejected as `422 invalid_request` - a behavior change for any caller that was previously
  sending values in that range (none are expected to exist, given these are short operational
  identifiers and realistic token counts, but it is a real, if unlikely, compatibility change).
- Chunked-transfer-encoding bodies with no `Content-Length` still bypass the size cap - a known,
  accepted residual gap, not resolved by this ADR. Closing it fully is future work, only worth
  doing against a concrete threat model that changes this service's deployment assumptions (e.g. no
  longer sitting behind an authenticated gateway).
