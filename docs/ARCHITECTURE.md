# Architecture

## Context

Policy Model Router is a standalone HTTP service that decides which logical **model group**
(`fast-small`, `reasoning-medium`, `reasoning-strong`, `fast-structured-output`) is authorized to
serve a given LLM workload, before any inference call happens. It sits upstream of a model
gateway (LiteLLM in the target deployment) and downstream of the agents that need a routing
decision.

```mermaid
flowchart LR
    Agent["Calling agent / workflow"] -->|"POST /route"| Router["policy-model-router"]
    Router -->|"selected_model_group"| Agent
    Agent -->|"provider/deployment call"| Gateway["Model gateway (LiteLLM)"]
    Router -.->|"loads at startup"| Policy["config/routing_policy.yaml"]
```

Upstream dependency: none. The service reads only its own routing policy file; it has no database,
queue, or outbound network call.

Downstream dependency: none from this service's point of view. Callers are responsible for taking
`selected_model_group` and resolving it to an actual provider/deployment/credential through their
own gateway. This service never calls a model and never sees prompts or completions.

`domain/enums.py` and `domain/routing.py` intentionally mirror the shape of
`credit_desk_contracts.*` from the separate `multi-agent-credit-desk` monorepo without importing
it, so this service has zero code dependency on that system while staying wire-compatible with it.

## Layers

```text
src/policy_model_router/
├── domain/
│   ├── enums.py         # DataClassification, RiskLevel, Workload, ModelGroup (closed vocabularies)
│   ├── catalog.py       # ModelGroupProfile, WorkloadRule, RoutingPolicy (declarative policy shape)
│   ├── routing.py       # RouteRequest, RouteDecision, RejectedCandidate, NoViableModelGroupError
│   └── constraints.py   # Ordered, pure eliminatory predicates (see ADR-0005)
├── application/
│   ├── ports.py         # Clock, IdGenerator, AvailabilityProvider protocols (ADR-0006)
│   └── route_model.py   # RouteModelUseCase: the two-step deterministic algorithm
├── adapters/
│   ├── routing_policy_loader.py  # YAML -> RoutingPolicy, fails closed on malformed/incomplete input
│   ├── clock.py                  # SystemClock
│   ├── id_generator.py           # Uuid4IdGenerator
│   ├── availability.py           # StaticAvailabilityProvider (no live health check; ADR-0006)
│   ├── rate_limiter.py           # InMemoryRateLimiter, per-process (ADR-0007)
│   └── tracing.py                # Opt-in, metadata-only tracing support
└── entrypoints/
    ├── contracts.py     # Pydantic wire contracts + domain <-> wire mapping
    ├── http.py           # FastAPI app: POST /route (auth + rate limit), /health, /readyz (ADR-0007)
    └── logging.py        # configure_logging(), called once at process startup
```

### Domain

Closed vocabularies (`enums.py`), immutable policy and request/decision Value Objects
(`catalog.py`, `routing.py`), and the ordered eliminatory constraint predicates (`constraints.py`).
No framework, transport, or persistence types. See ADR-0005 for the routing algorithm this layer
implements.

### Application

`RouteModelUseCase` coordinates one routing decision: look up the workload's mapped model group,
resolve each candidate's effective availability through the `AvailabilityProvider` port, run every
group through the domain constraints in order, select the mapped group if it survived, and raise a
domain error otherwise. `ports.py` defines the `Clock`, `IdGenerator`, and `AvailabilityProvider`
protocols the use case needs, on the consumer side, per the project's dependency rule.

### Adapters

`routing_policy_loader.py` parses and validates `config/routing_policy.yaml` into the domain's
`RoutingPolicy`, rejecting unknown fields and incomplete workload/model-group coverage.
`clock.py` and `id_generator.py` are the concrete `Clock`/`IdGenerator` implementations.
`availability.py::StaticAvailabilityProvider` is the only `AvailabilityProvider` implementation
today: it passes the policy's declared `available` flag through unchanged (ADR-0006).
`rate_limiter.py::InMemoryRateLimiter` is a per-process, fixed-window limiter consumed directly by
the HTTP entrypoint (ADR-0007). `tracing.py` provides metadata-only tracing per
`docs/LLM_OBSERVABILITY.md`.

### Entrypoints

`http.py` is the only entrypoint: a FastAPI app exposing `POST /route`, `GET /health`, and
`GET /readyz`. Its lifespan hook loads the routing policy, the required API key, and the rate
limiter once at startup, and fails fast if the policy is missing/invalid or the API key is not
configured. `POST /route` requires the `X-API-Key` header and is rate-limited per
`(client IP, agent_name)`; `/health` and `/readyz` require neither (ADR-0007). `contracts.py`
defines the closed Pydantic request/response schemas and the mapping to/from domain types.
`logging.py` configures structured logging once per process.

## Dependency rule

```text
entrypoints -> application -> domain
adapters    -> application/domain
domain      -> no outer layer
```

Enforced by `scripts/validate_architecture.py` as part of the quality gate.

## Cross-cutting decisions

- **Configuration**: `ROUTING_POLICY_PATH`, `APP_ENV`, `LOG_LEVEL`, `LOG_FORMAT`, `API_KEYS`,
  `RATE_LIMIT_MAX_REQUESTS`, `RATE_LIMIT_WINDOW_SECONDS` as environment variables; no other runtime
  configuration.
- **Logging**: structured JSON to stdout via `configure_logging()`; no prompt, response, or
  personal-data content is logged.
- **Tracing**: metadata-only by default; see `docs/LLM_OBSERVABILITY.md` for the content-tracing
  opt-in and its approval requirements.
- **Authentication**: per-agent API keys (`X-API-Key` header, looked up by the request's
  `agent_name`), required to start the service and checked with a constant-time comparison; not
  full IAM — no expiry, scoping, or identity assurance beyond "knew the right key" (ADR-0007,
  amended).
- **Rate limiting**: in-memory, fixed-window, per `(client IP, agent_name)`, per process — not
  shared across replicas (ADR-0007).
- **Errors**: domain errors (`NoViableModelGroupError`, `IncompleteRoutingPolicyError`) and HTTP
  boundary errors (`AuthenticationError`, `RateLimitExceededError`) are mapped to a stable JSON
  error envelope in `http.py`; no internal exception detail is returned to the caller.
- **Time**: UTC, timezone-aware `datetime` throughout (`RouteRequest.requested_at`,
  `RouteDecision.decided_at`).
- **Money**: `Decimal` for `estimated_cost_usd` and `max_cost_usd`.
- **Idempotency**: `POST /route` is a pure decision over caller-supplied input and the loaded
  policy; it has no side effects to deduplicate. `routing_decision_id` is generated per call and is
  not a dedupe key.
- **Packaging**: multi-stage, uv-based `Dockerfile`; the runtime `CMD` starts Uvicorn against
  `policy_model_router.entrypoints.http:app`.

## Related decisions

- [ADR-0001](adr/0001-clean-architecture.md): Clean Architecture dependency boundaries.
- [ADR-0004](adr/0004-litellm-provider-boundary.md): provider/deployment selection is out of
  scope; this service returns a logical model group only.
- [ADR-0005](adr/0005-deterministic-policy-routing.md): deterministic, ordered, fail-closed
  routing algorithm with no weighted fallback in the MVP; amended to make `risk_level` eliminatory.
- [ADR-0006](adr/0006-availability-provider-port.md): availability resolved through a pluggable
  `AvailabilityProvider` port; no live health check adapter yet.
- [ADR-0007](adr/0007-http-boundary-hardening.md): per-agent API keys (amended from a single
  shared secret), in-memory per-instance rate limiting, and `/health`/`/readyz` endpoints.
- [architecture-blueprint.md](architecture-blueprint.md): the data-classification authorization
  invariant this router enforces on behalf of the platform.

## Known gaps

Tracked debt, not yet implemented. Each item is a deliberate scope boundary, not an oversight, but
should not be assumed fixed:

| Gap | Current state | Consequence |
|---|---|---|
| No live availability signal | `AvailabilityProvider` (ADR-0006) is a real seam, but the only shipped implementation (`StaticAvailabilityProvider`) still just passes through the static YAML flag; nothing polls provider/gateway health | A group can be selected while its actual deployments are down; the policy file must be edited and the service redeployed to reflect an outage. Not resolved: no real health-check target exists yet to poll — adding one now would mean integrating against a system that isn't there |
| Rate limiter state is per-process | `InMemoryRateLimiter` (ADR-0007) has no shared store across replicas or worker processes | A multi-replica deployment enforces the configured limit *per replica*, not cluster-wide; a client can multiply its effective quota by however many instances it can reach. Not resolved: closing this needs a new infrastructure dependency (e.g. Redis) and a deployment-topology decision, not a code-only fix |
| `/readyz` is a shallow check | Returns ready once startup completed; there is no external dependency to probe (ADR-0004) | Cannot detect a policy that loaded successfully but is semantically wrong for the environment |

**Resolved:** the API key was a single shared secret for the whole service; ADR-0007's 2026-07-22
amendment replaced it with per-agent keys (`API_KEYS`), so one agent's key can be rotated or
revoked without affecting the others. This is still not full IAM — see the amendment's
Consequences for what remains out of scope.

Add fallback/scoring behavior, a live health check, per-agent auth, or a shared rate-limit store
only against a concrete requirement (an incident, a threat model, or an evaluation dataset for
tie-breaking) — not speculatively.

## Diagrams

The sequence for one routing decision, after the `X-API-Key` check and rate-limit check both pass
(ADR-0007):

```mermaid
sequenceDiagram
    participant A as Agent
    participant H as entrypoints/http.py
    participant U as RouteModelUseCase
    participant C as domain/constraints.py

    A->>H: POST /route
    H->>H: validate ModelRouteRequest (closed schema)
    H->>U: route(RouteRequest)
    U->>U: look up workload's mapped model group
    loop every model group in policy
        U->>C: run CONSTRAINTS in order
        C-->>U: first failure reason, or None
    end
    alt mapped group survived every constraint
        U-->>H: RouteDecision
        H-->>A: 200 with decision + rejected_candidates
    else mapped group was rejected
        U-->>H: raise NoViableModelGroupError
        H-->>A: 422 no_viable_model_group
    end
```
