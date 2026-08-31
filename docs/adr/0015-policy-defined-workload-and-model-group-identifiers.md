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

The domain types are `WorkloadId` and `ModelGroupId`, immutable `str` subclasses. The policy loader
uses Pydantic validation to construct those types from YAML and rejects malformed identifiers,
empty catalogs, undefined model-group references, and model groups that no workload can select.

A syntactically valid workload that is absent from the active policy reaches the deterministic
routing boundary and fails closed. The transport no longer decides authorization by maintaining a
hard-coded workload enum.

For source compatibility during the 0.x migration window, `Workload` and `ModelGroup` remain aliases
to the new identifier classes and the former credit-desk enum members remain class constants. These
constants are migration conveniences only and are explicitly not the complete vocabulary.

The legacy credit-desk policy is preserved under `examples/policies/credit-desk-routing.yaml`. A
gateway-oriented policy is provided under `examples/policies/gateway-generic.yaml`.

## Invariants preserved

- routing performs no inference;
- same request + same policy + same dependency state yields the same logical-group selection and
  reason codes;
- unknown workloads fail closed;
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
cross-service contracts predictable.

The API schema version stays at `1.0` because existing serialized workload/model-group values remain
valid strings and the new contract is an acceptance-superset rather than a field-shape break.
