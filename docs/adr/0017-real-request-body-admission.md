# ADR-0017: Bounded real-body admission before routing request parsing

## Status

Accepted on 2026-09-18 after explicit design approval. Implemented in the HTTP entrypoint with
synthetic ASGI and assembled FastAPI regressions; publication and production rollout are separate
actions. This supersedes ADR-0011's header-only size scope, not its per-IP quota placement.

## Date

2026-09-18

## Context

At main `858927a56310387d764c038da6bc7e4dce5ca631`, the pure ASGI
`_BodySizeAndIpRateLimitMiddleware` first checks the per-IP tier for exact `POST /route`,
then compares the first declared `Content-Length` with `app.state.max_request_body_bytes`
for all HTTP paths. It forwards the original `receive` without counting actual bytes.
The existing setting defaults to 16,384 bytes. JSON parsing, the per-agent tier, authentication,
signed authorization/control and routing occur downstream.

ADR-0011 explicitly accepted the absent-length/chunked residual gap using a deployment assumption
of an authenticated gateway in front of the Router. The current architecture also allows agents
to call this upstream PDP directly; the Governed LLM Gateway is the downstream PEP, not necessarily
an inbound HTTP proxy. The Docker command binds to all container interfaces, but this repository
does not define an ingress or establish public production exposure. These facts justify reviewing
defense in depth at the body boundary; they do not prove a production vulnerability, incident or
that operators have removed their existing ingress restrictions.

A bounded synthetic probe of the existing middleware used an eight-byte limit and a downstream
ASGI reader, not policy evaluation. A ten-byte body in two messages reached that reader both
without `Content-Length` and with a declared length of one. A declared length of ten returned
413 without reading or invoking downstream; a blocked IP returned 429 likewise. A non-decimal
length raised `ValueError` at this middleware boundary. Each case consulted the IP tier once.
These are ASGI-boundary observations, not proof that an HTTP server accepts malformed wire framing.
The 157 existing HTTP/startup regressions also passed without runtime changes.

The [ASGI HTTP specification](https://asgi.readthedocs.io/en/stable/specs/www.html#request-receive-event)
defines body messages, completion via `more_body` and server-owned transfer decoding. Counting
the bytes supplied through ASGI therefore covers a chunked body without implementing chunk decoding.
The [Uvicorn behavior contract](https://uvicorn.dev/server-behavior/#request-and-response-bodies)
also distinguishes body reception, response completion and transport flow control. This admission
must not be presented as a total upload timeout, process memory cap or ingress replacement.

## Decision

Implement the following approved, focused HTTP-entrypoint design:

1. Reuse `MAX_REQUEST_BODY_BYTES`, its existing positive validation, default and lifespan-owned
   state. No new dependency, runtime switch, policy schema, domain/application port or environment
   selector is needed. The byte limit is independent of token estimates and execution deadlines.
2. Keep the existing per-IP admission and bounded metric exactly once, before header checks or
   body reads for exact `POST /route`. Retain the per-agent tier/authentication positions,
   limiter backend/failure behavior and raw-peer-address trust. An oversized upload still consumes
   its one IP admission; body rejection must not create another quota consultation.
3. Retain the cheap declared-oversize precheck for all HTTP paths. Treat a single normalized,
   non-negative ASCII decimal `Content-Length` as a hint, never as proof of received size.
   Match header names case-insensitively and permit surrounding HTTP space/tab. Invalid values,
   repeated fields or comma lists reaching ASGI return a fixed 400 `invalid_request` envelope
   before reading, rather than leaking conversion errors. Compare large numeric declarations
   without unbounded integer conversion; a syntactically valid value above the cap returns 413.
   This conservative application rule does not claim to resolve HTTP request smuggling:
   transfer framing/ambiguity still belongs to the server and trusted proxy.
4. Add complete, bounded real-body admission for exact `POST /route`, including absent or
   underdeclared length. Read `http.request` messages until `more_body` is false, counting
   bytes, not characters. Empty/missing body and completion fields use ASGI defaults. Check the
   incoming message size against the remaining budget before copying it. Store accepted bytes
   in one bounded accumulator, not an unbounded list of frames. Accept equality with the cap.
5. If the next body message exceeds the cap, release the accumulator and emit the existing
   413 `payload_too_large` envelope once, without invoking the downstream parser/handler,
   consuming signed authority, emitting a routing decision or starting a success response.
   Do not drain an unbounded remainder, close the socket manually or replay the request.
   The HTTP server owns unread transport data and connection management.
6. Only after a complete in-limit body, deliver those identical bytes once to the existing
   pipeline in a completed ASGI body message. Keep replay state local to that request; subsequent
   reads must preserve authentic disconnect behavior using the original channel. This in-process
   delivery is not a second HTTP POST, policy retry, authorization cache or envelope reacquisition.
   JSON/schema validation and every existing authentication/authorization check remain intact.
7. On disconnect before completion, discard buffered content and pass the authentic disconnect
   event to the existing framework path, never manufacture a complete partial body or a success.
   On cancellation, discard content and propagate cancellation. Verify these paths with the
   assembled outer correlation middleware, not only the pure guard; they must not route or
   consume an envelope and must not produce duplicate responses. Do not broadly map unrelated
   receive/send failures to 400/413 or suppress them.
8. Keep other HTTP paths/methods at their existing declared-header precheck and normal framework
   behavior; do not wait for an upload on health/readiness/metrics or introduce authentication,
   quota or dependency probes there. Non-HTTP scopes pass through unchanged. The new real-body
   admission is for the existing JSON routing endpoint, not a generic future upload API.

## Alternatives considered

- Rely exclusively on ingress/authentication: retain those operational controls, but they are not
  configured by this repository and endpoint API-key checks run after body parsing.
- Continue trusting declared length: preserves the acknowledged ASGI byte-count gap.
- Count while downstream executes and catch overflow later: requires coordinating parser
  exceptions and possibly an already-started response. Complete pre-admission removes that race
  for this small, non-streaming JSON input without changing routing layers.
- Buffer an unlimited body or retain every frame: rejected; bounded payload bytes do not bound an
  unbounded collection of empty/small messages.
- Add a middleware package, change authentication order or a global body/deadline subsystem:
  unnecessary scope expansion for this entrypoint-only requirement.

## Consequences

In-limit requests keep the same bytes and existing wire/domain decisions; large absent-length or
underdeclared bodies newly receive 413. Invalid/duplicate length fields reaching the application
newly receive a stable 400. JSON/schema validation remains 422. These are compatibility tightenings
that are documented publicly and covered by regressions, not new policy denial decisions.

The locked Starlette `BaseHTTPMiddleware` passes a receive channel through `call_next`; the outer
correlation binder does not itself read the body. Inspection and assembled-app tests establish this
composition rather than relying on ADR-0011's historical unconditional-buffering explanation.
On an incomplete disconnect, FastAPI retains its existing 400 parsing-error path; no partial body
is completed, no routing/authority check occurs, and no custom disconnect success is synthesized.

Retained body storage and framework copies are proportional to the configured cap per request,
not one exact process allocation. The ASGI server may already hold an oversized incoming message;
this middleware cannot undo that allocation. Concurrent requests, parser objects, decompression,
headers and transport buffers are not a global memory budget. No content decoding is added.

Waiting for a small complete body still permits a slow uploader or many empty frames. Upload
duration/concurrency/ingress limits remain separate operational concerns; no implicit timeout,
hard return SLA, traffic admission cap or production topology is introduced.

## Security and privacy impact

This boundary only rejects input earlier. The Router remains the PDP and the Gateway remains the
PEP; Gateway allowed set remains a subset of Router-authorized set. No group fallback, provider
selection, business tool, authorization replay or signed-envelope schema change is introduced.

Bodies, credentials, arbitrary headers and raw exceptions must not be logged/traced. Preserve
the existing correlation/W3C metadata and stable bounded IP metrics, without labeling a body
rejection as a policy evaluation or fabricating verified authorization/violation evidence.
Tests use synthetic messages/credentials and controlled backends, never production secrets.

## Operational impact

Document the concrete target's callers, trusted proxy and ingress size/upload/concurrency controls
before rollout; do not infer public exposure from container binding or protection from the
downstream PEP. No cloud, credential authority, service or production deployment is activated here.
The intended implementation uses the existing cap in every environment rather than a silent
environment-dependent bypass. Review deployment-specific legitimate signed-request sizes before
changing that cap; this decision changes neither its value nor trust configuration.

Rollback to the prior application removes this defense and restores the documented residual gap;
it must not be described as retaining real-byte enforcement. Outer ingress restrictions should
remain in place. New body-consuming endpoints or mounting/path changes need a fresh scope review.

## Follow-up

Keep this implementation checkpoint separate from credentials/SpendGuard:

- `src/policy_model_router/entrypoints/http.py`: the declared-header validation and bounded
  admission inside the existing pure ASGI boundary; keep framework types in entrypoints.
- `tests/unit/test_http_body_admission.py`, `tests/unit/test_http.py` and
  `tests/unit/test_deployed_runtime_startup.py`: exact cap/cap+1, one oversized message, multi-message and empty-frame
  input, missing/misleading/invalid/duplicate lengths, disconnect/cancellation and concurrent
  isolation. Assert no downstream effect on overflow, one response, one IP admission and unchanged
  429 precedence, authentication, correlation/tracing, health/readiness/metrics and non-HTTP scopes.
  Exercise complete FastAPI/correlation composition, including normal JSON/schema failures and
  signed-authority non-consumption, with deterministic channels rather than sleeps.
- `docs/CONFIGURATION.md`, `docs/ARCHITECTURE.md`, `README.md` and `README.pt-BR.md`: update
  actual implemented scope/error/limits only after verification; retain ADR-0011 as historical
  context with an explicit supersession note when the new decision is accepted.
- Run focused synthetic HTTP/ASGI regressions, then the unchanged
  `uv run python scripts/quality_gate.py`, retaining the existing 88% coverage floor and all
  typing/security/architecture/vendor/schema/packaging checks. A future bounded real loopback
  HTTP chunked/keep-alive proof can verify server composition without production credentials.

Synthetic ASGI tests cover actual-byte admission and assembled middleware behavior. Controlled
deployed-composition tests verify that an oversized signed upload does not touch shared authority
state; an independent later in-limit POST can consume that authorization once, and another POST
is rejected as replay. This is not an automatic request retry or production certification.
Before rollout, review the concrete target's legitimate signed-request sizes and ingress controls.
Commit, push and PR publication still require a separate explicit request.
