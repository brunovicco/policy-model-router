# ADR-0005: Deterministic, ordered, fail-closed routing with no weighted fallback

- Status: Accepted
- Date: 2026-07-22

## Context

The code already implements and documents this decision (`domain/constraints.py`,
`domain/routing.py`, `application/route_model.py`, `config/routing_policy.yaml`) but no ADR
recorded it. This ADR is written retroactively from the existing implementation and comments to
close that gap; it does not change behavior.

A routing decision that selects a model group for an LLM workload needs to be reproducible and
auditable: the same request against the same policy must always produce the same decision, and a
rejection must state exactly which rule caused it. A scoring or ranking approach (e.g. picking the
"best" viable group by a weighted function of cost/latency/quality) would require evaluation data
that does not yet exist and would make rejections harder to explain to an auditor.

## Decision

1. **Ordered eliminatory constraints.** `domain/constraints.py` defines a fixed tuple of pure
   predicates (`CONSTRAINTS`): data classification, risk level, structured output, tool calling,
   context window, cost ceiling, latency ceiling, availability, agent allowlist — in that order. A
   candidate model group is rejected at the first constraint it fails; the rejection reason is
   exactly that constraint's message. Order is part of the contract: changing it changes which
   reason is reported for a candidate that fails more than one constraint.
2. **Two-step selection**, in `application/route_model.py`:
   - Every model group in the policy is evaluated against all constraints for the request.
   - The workload's declaratively mapped model group (`config/routing_policy.yaml`) is selected
     only if it survived every constraint. If it did not, the use case raises
     `NoViableModelGroupError` — a hard failure, not a silent reroute to another viable group.
3. **No weighted fallback in the MVP.** There is no scoring function and no substitution of a
   different group when the mapped one is rejected, even if another group is fully viable. This is
   an explicit MVP boundary, deferred to a later phase once per-workload evaluation data exists to
   justify how ties would be resolved and to support an audit trail for that choice.
4. **Fail closed on policy defects.** `routing_policy_loader.py` refuses to start the service on a
   missing, malformed, unknown-field, or incomplete YAML policy; `RouteModelUseCase.route` raises
   `IncompleteRoutingPolicyError` (mapped to HTTP 500) if a recognized workload has no mapping.
5. **`risk_level` is eliminatory** (see the 2026-07-22 amendment below).

## Consequences

- Decisions are reproducible: given a fixed policy and request, the selected group, the rejection
  set, and every rejection reason are deterministic. Only `routing_decision_id` and `decided_at`
  vary between identical calls.
- Every non-selected group appears in the response with a reason, which supports audit and
  debugging without additional logging of request content.
- A workload with a viable alternative model group still hard-fails if its *mapped* group is
  rejected. Callers must treat `no_viable_model_group` as a real failure to handle (retry with
  adjusted limits, escalate, or choose a different workload path), not as evidence of a bug.
- Introducing weighted scoring or automatic fallback later is a new ADR, not a silent code change,
  because it changes the audit story this ADR establishes.

## Amendment (2026-07-22): `risk_level` becomes eliminatory

This ADR originally shipped with `risk_level` validated but not eliminatory, flagged as a known
gap with an explicit note that turning it into a constraint would need "its own ADR update or
amendment" rather than a silent code change. This amendment is that update.

**Decision.** `ModelGroupProfile` gained `authorized_risk_levels: frozenset[RiskLevel]`, mirroring
`authorized_data_classifications` in shape but independent in meaning: a model group can be fully
cleared for the data involved and still be unauthorized for a high-stakes decision. A new
`check_risk_level` predicate was inserted into `CONSTRAINTS` immediately after
`check_data_classification` — both are authorization-style checks, evaluated before the
functional/capacity checks that follow.

**Rationale for the shipped values.** `config/routing_policy.yaml` authorizes `fast-small` and
`fast-structured-output` up to `medium` risk only, `reasoning-medium` up to `high`, and
`reasoning-strong` for all tiers including `critical`. The rationale is decision-quality assurance,
not data protection: a cheaper/faster model is a worse fit for a high-stakes decision regardless of
whether the data itself is sensitive. This mirrors the existing pattern of restricting weaker
groups more, but is a distinct axis from data classification and must be reasoned about separately
when the policy changes.

**Consequences.** A request can now be rejected for risk reasons even when every other constraint
passes (`not authorized for risk level 'critical'`). Callers that previously ignored `risk_level`
in their own logic (relying on this router to be permissive) must now expect `POST /route` to
reject high-risk requests mapped to lower-tier groups, and should route those workflows through a
workload/policy configuration that maps to `reasoning-strong` if `critical`-risk traffic is
expected.
