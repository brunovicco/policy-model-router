# ADR 0015 — Policy-defined workload and model-group identifiers

Status: Accepted  
Date: 2026-08-31

## Context

The original router mirrored the `multi-agent-credit-desk` vocabulary with closed Python `StrEnum`
types for five workloads and four logical model groups. That made the service deterministic, but it
also made a domain-specific example the system-wide vocabulary. Every new workload or logical group
required a code release even when the routing algorithm and governed constraints were unchanged.

The Governed LLM Gateway requires the Policy Model Router to act as a reusable Policy Decision Point
(PDP). The gateway asks whether a policy-defined workload such as `agent.orchestration`,
`rag.answer`, or `security.analysis` may use a logical model group. The gateway must never need a
credit-desk translation layer.

`DataClassification`, `RiskLevel`, and `ReasonCode` are different: they are controlled governance
vocabularies and remain closed enums.

## Decision

API schema `1.0` treats workload and model-group names as validated policy identifiers rather than
closed Python enums.

Identifiers:

- are 1–128 characters;
- use lowercase ASCII letters, digits, `.`, `_`, or `-`;
- begin and end with a letter or digit;
- are represented on the wire and in YAML as strings;
- become recognized only when declared by the active routing policy.

New workload identifiers are additionally namespace-qualified with at least one `.` separator. The
five former credit-desk workloads remain valid without a namespace during the 0.x compatibility
window. Model-group identifiers do not require namespace qualification.

The domain types are `WorkloadId` and `ModelGroupId`, immutable `str` subclasses. The policy loader
uses Pydantic validation to construct those types from YAML and rejects malformed identifiers,
empty catalogs, undefined model-group references, and model groups that no workload can select.

A syntactically valid namespace-qualified workload that is absent from the active policy reaches the
deterministic routing boundary and fails closed. The transport no longer decides authorization by
maintaining a hard-coded workload enum. A new unqualified workload is rejected as malformed before
routing; this preserves the existing 0.x validation behavior for arbitrary underscore-delimited
strings while providing a collision-resistant namespace for new policy vocabulary.

For source compatibility during the 0.x migration window, `Workload` and `ModelGroup` remain aliases
to the new identifier classes and the former credit-desk enum members remain class constants. The
compatibility classes remain iterable over those former members so existing `set(Workload)` and
`set(ModelGroup)` consumers continue to work. That iterable surface is migration-only and is not an
authorization vocabulary.

The legacy credit-desk policy is preserved under `examples/policies/credit-desk-routing.yaml`. A
gateway-oriented policy is provided under `examples/policies/gateway-generic.yaml`.

## Invariants preserved

- routing performs no inference;
- same request + same policy + same dependency state yields the same logical-group selection and
  reason codes;
- unknown well-formed workloads fail closed;
- data classification and risk remain controlled vocabularies;
- policy ID, version, and SHA-256 digest continue to travel with decisions;
- the router selects only a logical group, never a provider or concrete model;
- the downstream gateway may restrict the authorized set but may never broaden it.

## Consequences

Adding a workload or model group is now a policy/configuration change when no new capability or
constraint semantics are required. Consumers no longer need a router release merely to introduce a
new workload identifier.

Policies become the vocabulary authority, so policy review and provenance become even more
important. Identifier syntax is intentionally constrained to keep metrics, logs, policies, and
cross-service contracts predictable. Namespace-qualified workloads also reduce accidental naming
collisions across independently developed gateway consumers.

The API schema version stays at `1.0` because every previously valid serialized workload/model-group
value remains valid and the field shapes are unchanged. The new workload namespace rule applies only
to identifiers that were not part of the former closed 0.x workload vocabulary.
