# ADR-0006: Availability resolved through a pluggable port, no live health check yet

- Status: Accepted
- Date: 2026-07-22

## Context

`ModelGroupProfile.available` was a static bool read directly from `config/routing_policy.yaml`,
consumed by `domain/constraints.py::check_availability`. This was flagged as a known gap: a group
could be selected while its actual deployments were down, because nothing polled provider or
gateway health, and the policy file had to be hand-edited and the service redeployed to reflect an
outage.

Closing that gap properly — an adapter that calls out to a provider or gateway to ask "is this
group actually healthy right now" — is a real integration with its own failure modes: what to call,
how to time out and retry, what to do when the health check itself is unreachable, and how to cache
results so every routing decision doesn't trigger a fresh network call. None of that exists yet,
and per ADR-0004 this service currently has **no outbound network dependency at all** — introducing
one is a decision with its own tradeoffs (latency added to every `/route` call, a new failure mode
for the router itself, credentials to manage for the health-check target) that shouldn't be made
implicitly while fixing a documentation gap.

## Decision

Introduce the seam without introducing the network call:

- `application/ports.py` gains an `AvailabilityProvider` protocol:
  `is_available(model_group, declared_available) -> bool`.
- `RouteModelUseCase` takes an `AvailabilityProvider` as a required constructor argument and, for
  every candidate model group, computes an effective profile via `dataclasses.replace(profile,
  available=self._availability.is_available(model_group, profile.available))` before running it
  through `CONSTRAINTS`. `check_availability` itself is unchanged — it still just reads
  `profile.available` off whatever profile it's given.
- `adapters/availability.py::StaticAvailabilityProvider` is the only implementation shipped today:
  it returns `declared_available` unchanged. Behavior is identical to before this ADR.
- `entrypoints/http.py`'s lifespan wires `StaticAvailabilityProvider()` into the use case.

## Consequences

- No behavior change today: `available` is still exactly what `config/routing_policy.yaml` says,
  still requires a manual edit and redeploy to change, and this service still makes zero outbound
  network calls (ADR-0004's boundary is intact).
- A future dynamic adapter (e.g. one that calls the LiteLLM gateway's health endpoint, with an
  explicit timeout, bounded retries, and a cache to avoid a network round trip per `/route` call)
  is now a new adapter behind an existing port, not a change to the use case, the domain
  constraints, or their tests. That adapter is its own ADR when it exists — this one only
  authorizes the seam, not the integration.
- Tests exercise the seam directly: `tests/unit/test_route_model.py` includes a fake
  `AvailabilityProvider` that overrides a policy-declared-available group to unavailable, proving
  the use case actually consults the port rather than reading `profile.available` on its own.

## Amendment (2026-07-22): the port is async, ahead of any implementation that needs it

A code review of the shipped seam noted that `AvailabilityProvider.is_available` and
`RouteModelUseCase.route` were both synchronous, called from an `async def` FastAPI handler. That
was harmless with `StaticAvailabilityProvider` (no I/O), but a future adapter calling a real
provider/gateway health endpoint would have to either block the event loop on every `/route`
request or force a disruptive signature change once that adapter existed.

**Decision.** `AvailabilityProvider.is_available` and `RouteModelUseCase.route` are now `async
def`. `StaticAvailabilityProvider.is_available` awaits nothing and returns immediately - identical
behavior to before, just through an `async` method. `entrypoints/http.py`'s `route` handler now
`await`s `use_case.route(...)`.

**Consequences.** No behavior change today, same as the original ADR. A future live-health adapter
can now `await` its own network call directly, with bounded timeout/retry of its own choosing,
without a second signature migration. `tests/unit/test_availability.py` and
`tests/unit/test_route_model.py` were updated to `await` accordingly; this is a purely mechanical
consequence of async propagating through the call chain, the same kind of change ADR-0008 made to
`RateLimiter.allow`/`ping`.
