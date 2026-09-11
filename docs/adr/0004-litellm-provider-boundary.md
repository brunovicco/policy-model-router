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

## Amendment (2026-09-11): the downstream gateway is named, and it is not LiteLLM

This ADR's Context states that "the organization already operates a model gateway (LiteLLM)
responsible for provider/deployment routing, credentials, and failover," and its Decision assigns
provider selection, failover and the inference call to LiteLLM by name. Both describe the
deployment as it was assumed when the ADR was written retroactively from the code.

The downstream that actually consumes this service is
[governed-llm-gateway](https://github.com/brunovicco/governed-llm-gateway), which is
provider-neutral and carries its own adapters for OpenAI, Anthropic, Gemini, Groq, OpenRouter and
NVIDIA. It does not use LiteLLM.

**Decision.** The boundary this ADR records is unchanged: this service returns a logical model
group, never a provider, deployment or credential, and makes no outbound call. What changes is the
name on the other side of it.

The authority chain is a Policy Decision Point / Policy Enforcement Point pair:

```text
Verifiable AI Governance -> Policy Model Router (PDP) -> Governed LLM Gateway (PEP) -> provider
```

with the permanent invariant

```text
Gateway allowed set  ⊆  Policy Router authorized set
```

The gateway may reject more deployments than this router authorized; it may never broaden or
synthesize authorization. That is why a rejection here carries the same provenance as an acceptance
(ADR-0009): the enforcement point must be able to prove *which* policy denied a call. The binding
is versioned against `POST /route` wire schema `1.0` and documented on the gateway side in
`docs/architecture/PDP_PEP_CONTRACT_DRAFT.md`.

**LiteLLM remains possible, one layer lower.** The gateway's own ADR-0004 defines an explicit
OpenAI-compatible adapter family for "compatible custom endpoints," and LiteLLM's proxy speaks that
wire, so a LiteLLM deployment can sit *beneath* the gateway as one more provider endpoint - a
registry entry plus a credential, no code. Two caveats belong in that decision when someone makes
it: the gateway requires an absolute HTTPS endpoint, so a plain `http://localhost:4000` proxy will
not load; and LiteLLM's own routing and fallback should be disabled, one alias per gateway registry
entry. Otherwise two layers perform failover independently and the gateway's deployment-level
provenance stops being true - it would record the deployment it selected while LiteLLM silently
served a different one, which would quietly invalidate the evidence-driven ranking built on those
records.

**Consequences.** No behavior change: this service still has no outbound network dependency and no
credentials to manage. The filename is retained so existing references keep resolving, even though
it now names a system this decision no longer points at.

Note that the chain above is not yet operational end to end. The gateway does not forward the
signed runtime-authorization envelope to this service, so a deployment running with
`RUNTIME_AUTHORIZATION_REQUIRED=true` answers it `403` and the gateway fails closed before any
provider call. That gap is tracked on the gateway's roadmap, not here.
