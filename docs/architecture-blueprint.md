# Architecture blueprint (local scope)

This file exists because `domain/catalog.py` and `domain/constraints.py` reference
"`docs/architecture-blueprint.md` section 2.3" for the data-classification authorization rule
this router enforces. That reference predates this file; this document is the local, authoritative
copy of the one rule this repository depends on. It is not a copy of any platform-wide or
multi-service architecture document — this service has no code or documentation dependency on the
separate `multi-agent-credit-desk` monorepo (see ADR-0001 and the project README).

## Section 2.3: Data-classification authorization for model groups

**Rule.** A model group is authorized for a data classification (`public`, `internal`,
`confidential`, `restricted`) only if every deployment in that group's provider pool, in the
environment the policy file describes, is cleared to process data at that classification.

This is a conjunction, not a majority: one deployment in the pool that is not cleared for
`restricted` means the whole group is not authorized for `restricted`, regardless of how many
other deployments in the pool are cleared.

**Split of responsibility.**

- **This router's half** (implemented here): `ModelGroupProfile.authorized_data_classifications`
  in `domain/catalog.py` is the pre-computed result of that conjunction for each model group, for
  the environment described by the loaded `config/routing_policy.yaml`.
  `domain/constraints.py::check_data_classification` rejects a candidate group whose authorized set
  does not include the request's classification. See ADR-0005.
- **The gateway's half** (out of scope, per ADR-0004): deciding *which* deployments belong to each
  group's provider pool, and whether each deployment is actually cleared for a classification
  (contractual/regional/certification status), is the model gateway's (LiteLLM) configuration, not
  this service's. This router only consumes the outcome of that decision, encoded in the policy
  file's `authorized_data_classifications` per group.

**Example, from the shipped policy** (`config/routing_policy.yaml`):

- `fast-small` and `fast-structured-output` are backed only by external providers with no
  confidential/restricted clearance in the shipped example, so their authorized set is
  `[public, internal]`.
- `reasoning-medium` and `reasoning-strong` include a locally hosted deployment in their pool that
  is cleared for higher classifications, so their authorized set extends to
  `[public, internal, confidential, restricted]`.

**Operational implication.** Whenever a deployment's clearance changes, or a deployment is added to
or removed from a group's provider pool, `authorized_data_classifications` for the affected model
group(s) in `config/routing_policy.yaml` must be updated to match. This router has no way to detect
that the policy file is stale — it trusts the file as the authorized statement of clearance for
its environment.
