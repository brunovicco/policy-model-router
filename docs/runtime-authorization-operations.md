# Runtime authorization operations

## Production settings

```bash
export RUNTIME_AUTHORIZATION_REQUIRED=true
export RUNTIME_AUTHORIZATION_ISSUER='verifiable-ai-governance:production'
export RUNTIME_AUTHORIZATION_AUDIENCE='policy-model-router'
export RUNTIME_AUTHORIZATION_TRUSTED_KEY_SET_PATH='/etc/policy-model-router/runtime-authorization-keys.json'
export RUNTIME_AUTHORIZATION_AGENT_BINDINGS_JSON='{"Agente de Parecer de Crédito PJ":"<governance-agent-uuid>"}'

export RUNTIME_AUTHORIZATION_EXPECTED_POLICY_ID='baseline-governance-policy'
export RUNTIME_AUTHORIZATION_EXPECTED_POLICY_VERSION='1.0.0'
export RUNTIME_AUTHORIZATION_EXPECTED_POLICY_DIGEST='<same Governance policy digest>'
export RUNTIME_AUTHORIZATION_EXPECTED_CONTROL_CATALOG_ID='verifiable-ai-governance-baseline'
export RUNTIME_AUTHORIZATION_EXPECTED_CONTROL_CATALOG_VERSION='1.0.0'
export RUNTIME_AUTHORIZATION_EXPECTED_CONTROL_CATALOG_DIGEST='<same Governance catalog digest>'

export RUNTIME_AUTHORIZATION_CLOCK_SKEW_SECONDS=0
export RUNTIME_AUTHORIZATION_REPLAY_KEY_PREFIX='policy-model-router:runtime-auth:'
export REDIS_URL='redis://redis:6379/0'
```

Install Redis support for a deployed/shared verifier:

```bash
uv sync --extra rate-limit
```

P1.3 also adds a direct `cryptography` dependency. After applying the patch run:

```bash
uv lock
uv sync --locked --extra rate-limit --group dev
```

## Stable denial codes

Examples include:

- `runtime_authorization_required`
- `invalid_signature`
- `unknown_key`
- `key_revoked`
- `expired`
- `request_binding_mismatch`
- `agent_binding_mismatch`
- `governance_policy_mismatch`
- `replay_detected`
- `replay_store_unavailable`
- `selected_model_group_not_authorized`

P1.4 will turn these bounded codes into standardized governance violation events.
