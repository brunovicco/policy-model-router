# Privacy and data handling

This service never calls an LLM and never sees prompts or completions (ADR-0004). Its personal-data
surface is narrow: the caller's network address (for rate limiting) and whatever opaque identifiers
a caller chooses to send. Nothing here is a substitute for the calling system's own privacy review
of what it puts into `workflow_id`/`task_id`/`agent_name` - this service does not inspect, validate,
or interpret their content beyond treating them as identifiers.

## Data inventory

| Data category | Source | Purpose | Legal/contractual basis | Destination | Retention | Deletion method |
|---|---|---|---|---|---|---|
| Client IP address | TCP peer address of the `POST /route` caller (`http_request.client.host`) | Rate-limiting key, both tiers (ADR-0007, ADR-0008) | Legitimate interest (abuse/DoS mitigation) | In-memory dict (default) or Redis, if `REDIS_URL` is configured | One rate-limit window (`RATE_LIMIT_WINDOW_SECONDS`, default 60s) | Automatic: Redis key `PEXPIRE`, or in-memory fixed-window/LRU eviction |
| Client IP address (fail-open case only) | Same as above | Correlating repeated Redis-backend failures in logs | Legitimate interest (operability) | Structured logs (stdout) | Governed by the log pipeline's own retention, not this service's | Not stored by this service; only an HMAC-keyed, non-reversible fingerprint (12 hex chars) is logged, never the raw IP |
| `agent_name` | Request body, matched against `API_KEYS` | Authentication, per-agent rate limiting, allowlists | Contractual (identifies the calling system, not a natural person) | In-memory (`API_KEYS`), Redis rate-limit key if configured | Same as the client-IP rate-limit entry above | Same as above |
| `workflow_id` / `task_id` | Request body, caller-supplied | Echoed back in the decision record for the caller's own correlation | Contractual | Not persisted or logged by this service; present only in the request/response of one call | N/A - not stored | N/A |
| `X-Correlation-Id` / generated correlation ID | Request header (reused) or generated (`uuid4`) | Joining this service's log lines for one request | Legitimate interest (operability) | Structured logs (stdout), response header | Governed by the log pipeline's own retention | N/A - not stored by this service itself |
| `X-API-Key` | Request header | Authentication | Contractual | Compared in memory (`secrets.compare_digest`); never logged | N/A - not stored beyond the request | N/A |

Routing-decision content itself (`workload`, `risk_level`, `data_classification`, cost/latency
figures, the selected/rejected model groups) is operational metadata about an LLM call being
routed, not personal data.

## Controls

- Data minimization: only the client IP and caller-supplied opaque identifiers are processed; no
  prompt, completion, or document content ever reaches this service.
- Access control: `API_KEYS` gates `/route`; `/health`, `/readyz`, and `/metrics` are
  unauthenticated by design (see README's Authentication and rate limiting section) and should be
  restricted at the network/ingress layer in production.
- Encryption in transit: this service does not terminate TLS itself; deploy it behind a TLS-
  terminating gateway/ingress. `REDIS_URL` should point at a TLS-enabled Redis in production if the
  network path is not otherwise trusted.
- Encryption at rest: not applicable - this service holds no durable storage of its own; Redis (if
  configured) is external infrastructure whose encryption-at-rest is the operator's responsibility.
- Masking/tokenization: the client IP is never logged in raw form on the fail-open path (see the
  data inventory above); the Redis key itself does contain the raw IP as a substring, which is why
  Redis access must be restricted to this service and its operators.
- Non-production data strategy: `config/routing_policy.yaml` and all test fixtures use synthetic
  values; `.claude/rules/security-privacy.md` prohibits production personal data in tests.
- Logging and tracing restrictions: structured JSON logs to stdout only (`entrypoints/logging.py`);
  no prompt, response, or personal-data content is logged; see [Prohibited logging](#prohibited-logging)
  below. This service has no tracing adapter (the previously-shipped, unused Langfuse adapter was
  removed - see the Changelog).
- Data-subject deletion/anonymization: no durable per-caller record exists to delete beyond the
  rate-limit window described above, which expires automatically.
- External processors: Redis, if `REDIS_URL` is configured, is the only external system this
  service writes to; it is operator-provisioned infrastructure, not a third-party processor this
  project contracts with.
- Incident-response owner: see [`SECURITY.md`](../SECURITY.md) for how to report a suspected
  incident or vulnerability.

## Prohibited logging

Secrets, authentication headers, personal identifiers, full financial identifiers, complete request/response payloads, prompts, and model outputs containing sensitive data.
