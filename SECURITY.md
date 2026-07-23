# Security policy

## Supported versions

This project has not yet made a tagged release; `main` is the only supported line. Security fixes
land there and are not backported.

## Reporting a vulnerability

Report suspected vulnerabilities privately through
[GitHub Security Advisories](https://github.com/brunovicco/policy-model-router/security/advisories/new)
for this repository ("Report a vulnerability"). Do not open a public issue for a suspected
vulnerability.

Include, where relevant:

- affected version or commit;
- the endpoint, component, or file involved;
- steps to reproduce, or a minimal proof of concept;
- the impact you assess (e.g. authentication bypass, information disclosure, denial of service).

## What to expect

This is an independently maintained project without a dedicated security team or a fixed SLA.
Reports are read and triaged as time allows; a genuine, reproducible vulnerability is prioritized
over other work. You will get an acknowledgment through the advisory thread once it has been
reviewed.

## Scope

In scope: the code in this repository (`src/`, `scripts/`, `Dockerfile`, `docker-compose.yml`, CI
workflows). Out of scope: vulnerabilities in third-party dependencies (report those upstream; see
`uv run python scripts/quality_gate.py --check dependencies` for this project's own dependency
audit) and issues that require an already-compromised deployment environment (e.g. a leaked
`API_KEYS` value) to exploit.
