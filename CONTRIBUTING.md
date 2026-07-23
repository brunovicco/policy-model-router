# Contributing

## Before you start

Read [`AGENTS.md`](AGENTS.md) — it is the engineering contract for this repository: architecture
and dependency-direction rules, the working method, and the quality gate. `.claude/rules/*.md`
contains the detailed, path-scoped conventions referenced from there (architecture, Python style,
adapters, API boundaries, testing, observability, security/privacy, git collaboration).

## Setup

```bash
git clone https://github.com/brunovicco/policy-model-router.git
cd policy-model-router
uv sync --frozen
```

See [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) for running the service locally, including the
optional Redis-backed rate limiter (`docker compose up -d redis`).

## Quality gate

Every change must pass the full gate before it is proposed:

```bash
uv run python scripts/quality_gate.py
```

Use `--check <name>` (see `--list` for the full set: lock, lint, format, architecture, mcp,
governance, loop-schema-vendor, loop-contracts, typing, tests, security, dependencies, packaging)
to iterate on one check at a time.

## Making a change

1. Confirm the requested behavior, constraints, and acceptance criteria before writing code.
2. For non-trivial changes, write a short plan first — see `AGENTS.md`'s working method.
3. Keep the allowed dependency direction: `entrypoints -> application -> domain`,
   `adapters -> application/domain`, and `domain` importing from no outer layer. This is checked
   by the `architecture` quality-gate step.
4. Add regression tests for fixes and behavior tests for new work. Unit tests do not use real
   network, database, queue, clock, or randomness.
5. Add an ADR under `docs/adr/` for a material architectural decision, following the existing
   numbered format (`docs/adr/0001-...md` onward).
6. Update `README.md` and `README.pt-BR.md` together when user-facing behavior changes — they are
   meant to stay in sync.
7. Run the full quality gate and review your own diff for scope, security, and compatibility
   before opening a pull request.

## Pull requests

- Keep diffs focused; do not bundle unrelated refactors or formatting changes.
- Write commit messages and PR descriptions in English, explaining intent rather than mechanics.
- Note any security-relevant surface touched (authentication, external input, dependencies,
  secrets) in the PR description.

## Reporting a vulnerability

Do not open a public issue. See [`SECURITY.md`](SECURITY.md) for private reporting instructions.
