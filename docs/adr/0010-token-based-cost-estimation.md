# ADR-0010: Cost is estimated from tokens and per-token rates, not a flat number per group

- Status: Accepted
- Date: 2026-07-22

## Context

`domain/constraints.py::check_max_cost` compared `ModelGroupProfile.estimated_cost_usd` - one
fixed number per model group, e.g. `"0.20"` for `reasoning-strong` - against the request's
`max_cost_usd` ceiling. A request estimated at 1,000 tokens and one estimated at 100,000 tokens
were charged identically by this constraint, because the group's cost was never actually a
function of the request. For a router whose stated job is to keep GenAI spend inside a caller-set
budget, a cost estimate that ignores the size of the call is not meaningfully doing that job -
flagged directly in a review of the service as one of its most consequential gaps.

## Decision

**Cost is priced per token, not per group.** `config/routing_policy.yaml` replaces each group's
single `estimated_cost_usd` with `input_cost_usd_per_million_tokens` and
`output_cost_usd_per_million_tokens` (both `Decimal`, `>= 0` - zero is allowed, unlike the old
field's `> 0`, so a free/local model group can be declared honestly).
`adapters/routing_policy_loader.py::_ModelGroupProfileConfig` validates both;
`domain/catalog.py::ModelGroupProfile` carries both and gains a pure method,
`estimated_cost(*, input_tokens, output_tokens) -> Decimal`, computing
`input_tokens * input_rate / 1_000_000 + output_tokens * output_rate / 1_000_000`. No reasoning-
token or cache-discount modeling: this is the smallest change that makes the estimate a function of
request size, not a general pricing engine, consistent with `.claude/rules/architecture.md`
("abstractions for demonstrated variation, not ritualistically").

**The request must estimate its output size too.** Input size was already available as
`context_tokens_estimated`; there was no equivalent for expected output. `domain/routing.py`'s
`RouteRequest` and `entrypoints/contracts.py`'s `ModelRouteRequest` both gain
`max_output_tokens_estimated: int` (`>= 0`, required - not defaulted to zero, since a silent
default would silently under-estimate cost for any caller that omits it, which is worse than
forcing every caller to state it).

**The constraint changes its input, not its shape.** `check_max_cost` now calls
`profile.estimated_cost(input_tokens=request.context_tokens_estimated,
output_tokens=request.max_output_tokens_estimated)` and compares the result against
`request.max_cost_usd`, in the same position in `CONSTRAINTS`' fixed evaluation order (ADR-0005) as
before. The rejection message reports the computed estimate, not a static number.

## Consequences

- `check_max_cost` now rejects (or admits) based on the actual size of the call being routed, not
  a number that never changed regardless of request. Two requests to the same workload with very
  different token counts can now land on different sides of a caller's cost ceiling, which they
  could not before.
- `ModelRouteRequest` gained a new required field: every caller must now estimate its own expected
  output size. This is a breaking addition to the wire contract, tracked alongside ADR-0009's
  contract changes as the point where this repository's response *and* request contracts diverged
  from the `credit_desk_contracts` mirror it was originally field-for-field compatible with (see
  `entrypoints/contracts.py`'s module docstring and ADR-0009's Consequences).
  `multi-agent-credit-desk` needs updating to send it before that monorepo can call this service.
- The shipped `config/routing_policy.yaml`'s per-token rates
  (e.g. `reasoning-strong`: input `"2.00"`, output `"8.00"` per million tokens) are, like
  `typical_latency_ms` before them, this router's own illustrative figures - a static input to a
  deterministic constraint, not a live pricing feed synced from any provider. Keeping them current
  as real provider pricing changes remains an operational task, not something this ADR automates.
- No change to constraint ordering, to any other constraint, or to the routing algorithm itself
  (ADR-0005): only `check_max_cost`'s inputs changed.
