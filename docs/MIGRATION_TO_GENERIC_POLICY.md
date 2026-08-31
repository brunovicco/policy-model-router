# Migration to policy-defined workloads and model groups

## What changed

Policy Model Router API `1.0` no longer defines a closed Python enum for workloads or logical model
groups. The active routing policy is now the authority for those identifiers.

Still controlled by code:

- `DataClassification`
- `RiskLevel`
- `ReasonCode`
- request/response schema shape
- deterministic constraint order and fail-closed behavior

Defined by policy:

- workload identifiers such as `rag.answer` or `agent.orchestration`
- logical model-group identifiers such as `balanced` or `agentic-strong`
- workload-to-group mappings
- group capabilities, authorization constraints, and static limits

## Compatibility

Existing 0.x imports continue to work during migration:

```python
from policy_model_router.domain.enums import ModelGroup, Workload

assert Workload.CASHFLOW_ANALYSIS.value == "cashflow_analysis"
assert ModelGroup.REASONING_STRONG.value == "reasoning-strong"
```

The compatibility classes also remain iterable over the former closed vocabulary, so existing code
such as `set(Workload)` and `set(ModelGroup)` keeps working during the 0.x migration window. That
iteration is not the complete vocabulary and must not be used to authorize new identifiers.

New code should prefer:

```python
from policy_model_router.domain.identifiers import ModelGroupId, WorkloadId

workload = WorkloadId("agent.orchestration")
model_group = ModelGroupId("agentic-strong")
```

HTTP callers continue sending strings, so existing valid JSON payloads remain valid.

### Workload namespaces

New workload identifiers are namespace-qualified with at least one `.` separator, for example
`rag.answer`, `security.analysis`, or `agent.tool-use`. The five former credit-desk workload values
remain accepted without a namespace for backwards compatibility:

- `document_extraction`
- `cashflow_analysis`
- `findings_correlation`
- `opinion_drafting`
- `json_repair`

This lets the router accept an open policy vocabulary without making arbitrary unqualified strings a
valid workload namespace. Logical model-group identifiers do not require a `.` and may use names
such as `balanced`, `reasoning-strong`, or `agentic-strong`.

## Policy migration

The former credit-desk policy is preserved at:

`examples/policies/credit-desk-routing.yaml`

A generic policy intended for Governed LLM Gateway integration is available at:

`examples/policies/gateway-generic.yaml`

Select a policy using the existing `ROUTING_POLICY_PATH` deployment setting. No translation from
generic workloads to credit-desk workloads should be added to the gateway.

## Unknown workloads

A syntactically valid, namespace-qualified workload is accepted by the API contract but is not
authorized merely because it is well formed. If it is absent from the active policy, the router
fails closed before any model execution can occur.

Invalid identifier syntax, including a new unqualified workload name, is rejected at the
API/configuration boundary.

## Policy validation changes

The loader no longer checks coverage against Python enums. Instead it requires:

1. a non-empty model-group catalog;
2. a non-empty workload mapping;
3. every workload to reference a declared model group;
4. every declared model group to be reachable from at least one workload;
5. exact known schema fields and valid controlled vocabularies;
6. valid policy-defined identifiers.

This keeps the policy generic without accepting partial or internally inconsistent configuration.
