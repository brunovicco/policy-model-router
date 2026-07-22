# ADR-0004: Provider and deployment selection stays out of this service

- Status: Accepted
- Date: 2026-07-22

## Context

The code already implements and documents this decision (`domain/catalog.py`,
`entrypoints/http.py`, `config/routing_policy.yaml`) but no ADR recorded it. This ADR is written
retroactively from the existing implementation and comments to close that gap; it does not change
behavior.

An LLM workload ultimately needs a concrete provider, deployment, and credential to run. That
concern is separate from deciding *which class of model* a workload is allowed to use. Handling
both in one service would couple policy governance (data classification, cost/latency ceilings,
agent allowlists) to infrastructure concerns (provider outages, credential rotation, failover,
per-provider rate limits) that change on a different cadence and are owned by a different team.

The organization already operates a model gateway (LiteLLM) responsible for provider/deployment
routing, credentials, and failover.

## Decision

Policy Model Router selects and returns a **logical model group** (e.g. `reasoning-strong`), never
a provider, deployment, or credential. It does not call a model and never sees a prompt or a
completion.

Concretely:

- `domain/enums.py`'s `ModelGroup` is a closed set of logical names, independent of any provider.
- `ModelGroupProfile.authorized_data_classifications` encodes the router's half of the
  classification-authorization rule (see `docs/architecture-blueprint.md`): a group is authorized
  for a classification only if every deployment behind it, in the calling environment, is cleared
  for that classification. Which deployments back a group, and whether they are currently healthy,
  is configured and resolved by the gateway, not by this service.
- Provider selection, failover between deployments within a group, credential management, and the
  actual inference call are LiteLLM's responsibility.
- This service is called directly by agents over HTTP as plain infrastructure. It is not
  discovered through Agent Cards and does not participate in the A2A protocol as an agent.

## Consequences

- This service has no outbound network dependency and no credentials to manage; it can be tested,
  deployed, and reasoned about in isolation from provider availability.
- A model group's `available` flag (see ADR-0005 and the Known Gaps section of
  `docs/ARCHITECTURE.md`) reflects a policy-level decision ("this group's deployments are cleared
  for use in this environment"), not live provider health. Live health/failover is the gateway's
  job; if the gateway needs routing input from this service for that purpose, that is a future,
  explicit integration, not an implicit one.
- Callers must resolve `selected_model_group` to an actual provider/deployment through their own
  gateway; this service's response alone is not sufficient to make an inference call.
