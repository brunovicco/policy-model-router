# ADR-0009: Every routing decision carries the policy and deployment identity that produced it

- Status: Accepted
- Date: 2026-07-22

## Context

`ModelRouteDecision` carried `schema_version` (the wire contract's own version), a
`routing_decision_id`, a timestamp, and the outcome - but nothing identifying *which routing
policy* produced the decision, or *which deployment* of this service produced it. A review of the
service noted this gap concretely: two decisions produced before and after an edit to
`config/routing_policy.yaml` look identical in every field an auditor can inspect, so a decision
record alone cannot prove which version of the policy was active when it was made. For a service
whose entire value proposition is a deterministic, auditable routing decision, that is a real gap
between what the service claims (`docs/ARCHITECTURE.md`, the README) and what it can prove.

## Decision

**The routing policy declares its own identity; the loader adds tamper-evidence.**
`config/routing_policy.yaml` gains two required top-level fields, `policy_id` and `policy_version`
(both non-empty strings, validated in `adapters/routing_policy_loader.py::_RoutingPolicyConfig`).
`load_routing_policy` additionally computes `policy_digest` - `sha256:<hex>` of the raw file bytes,
*not* trusted from the file itself - so a decision is traceable to exactly what content was loaded,
independent of whether whoever edited the file remembered to bump `policy_version`.
`domain/catalog.py::RoutingPolicy` carries all three.

**The use case is also told its own deployment identity.** `RouteModelUseCase.__init__` now takes
`service_version` and `environment` as required keyword arguments, alongside the existing
clock/id-generator/availability ports. `entrypoints/http.py`'s `_lifespan` already computed both
(`_service_version()` via `importlib.metadata`, and `APP_ENV` for `configure_logging`) - they are
now also passed into the use case constructor, so the wiring is a single source of truth rather
than being resolved twice.

**All five travel on every decision.** `domain/routing.py::RouteDecision` gains `policy_id`,
`policy_version`, `policy_digest`, `service_version`, and `environment`;
`RouteModelUseCase.route` populates them from `self._policy` and the constructor arguments on
every decision it returns, success or (for `NoViableModelGroupError`, which still carries the
partial decision context via its own attributes) failure path.
`entrypoints/contracts.py::ModelRouteDecision` mirrors all five as required, non-empty string
fields on the wire response.

**This is a deliberate divergence from `credit_desk_contracts.routing`, not a silent one.**
`entrypoints/contracts.py`'s module docstring now says so explicitly:
`credit_desk_contracts.routing.RouteDecision` in the separate `multi-agent-credit-desk` monorepo
does not yet have these five fields, and this repository has no automated contract test against
that monorepo (a pre-existing gap this ADR does not close - see `docs/ARCHITECTURE.md`'s Known
Gaps). Adding fields to a response is additive and typically safe for consumers that ignore unknown
fields, but any consumer built against a byte-for-byte mirror of the old contract needs updating to
match.

## Consequences

- A routing decision is now provably tied to the exact policy content that produced it: two
  decisions can only be assumed equivalent to each other if `policy_digest` (not just
  `policy_version`) matches, closing the "author forgot to bump the version" failure mode.
- `config/routing_policy.yaml` requires `policy_id`/`policy_version`; any other policy file (custom
  deployments, local overrides) must add them or fail closed at load time, consistent with this
  loader's existing fail-closed posture.
- `RouteModelUseCase`'s constructor grew two required arguments; every call site (production
  wiring in `entrypoints/http.py`, and every test fixture building a `RouteModelUseCase` directly)
  needed updating - a one-time, mechanical migration, not a design concern.
- The response contract grew five required fields. This is the first change since the service
  shipped that is *not* field-for-field compatible with `credit_desk_contracts.routing`; closing
  that gap (a shared package, a published JSON Schema, or a contract test) remains future work,
  now made more urgent by this ADR rather than newly discovered by it.
- No change to the routing algorithm itself (ADR-0005): this ADR is entirely about what a decision
  record proves after the fact, not how the decision is made.
