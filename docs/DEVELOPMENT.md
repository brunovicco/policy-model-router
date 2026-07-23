# Development guide

## Setup

```bash
uv sync --frozen
```

## Run checks

```bash
uv run python scripts/quality_gate.py
```

## Container

```bash
docker build -t policy-model-router .
docker run --rm -p 8000:8000 \
  -e API_KEYS='{"credit-analysis-agent":"dev-local-key"}' \
  policy-model-router
```

`API_KEYS` is required - the service refuses to start without it (see
[Authentication and rate limiting](../README.md#authentication-and-rate-limiting)).

`Dockerfile` is a multi-stage, uv-based build: a `builder` stage installs the locked
dependencies and builds the package, then the resulting virtualenv and source are copied into a
slim, non-root runtime image. The container starts the FastAPI application with Uvicorn on port
8000. Adjust `.dockerignore` if new top-level files or directories need to be excluded from the
build context.

## Local configuration

Copy `.env.example` only when the application supports local dotenv loading. Never commit `.env` or real credentials.

## Optional local Redis (shared rate limiting)

Only needed to exercise `REDIS_URL`/`RedisRateLimiter` (ADR-0008); the default in-memory rate
limiter needs none of this.

```bash
docker compose up -d redis
uv sync --extra rate-limit
REDIS_URL=redis://localhost:6379/0 \
API_KEYS='{"credit-analysis-agent":"dev-local-key"}' \
uv run uvicorn policy_model_router.entrypoints.http:app --reload
```

`docker compose down` stops it. Unit tests never talk to a real Redis (they fake the client), but
`tests/integration/test_redis_rate_limiter_integration.py` does - with the container above running
and `REDIS_URL` set, `uv run pytest` picks it up automatically; without either, that module skips
itself instead of failing. `.github/workflows/quality.yml` runs a `redis:7-alpine` service and
installs the `rate-limit` extra, so it always runs (not skips) in CI.

## Claude Code

- Run `/memory` to confirm loaded instructions.
- Run `/hooks` to inspect configured hooks.
- Run `claude doctor` from the shell for a read-only installation and configuration check. Reserve
  interactive `/doctor` for cases that may need guided repair, and review its requested commands.
- Use `/plan-change` before complex work.
- Use `/quality-gate` before completion.
- Use `/prepare-pr` to produce a reviewable PR description.

### Isolating riskier changes in a worktree

For a larger or harder-to-reverse change, add `isolation: worktree` to
`.claude/agents/python-implementer.md`'s frontmatter before delegating the change. The subagent
then works from a temporary git worktree branched off the default branch instead of editing the
working tree directly; the worktree is cleaned up automatically if it makes no changes. This is
not the harness default because it changes where edits land - add it deliberately for a specific
change you want to inspect before merging into your working tree, then remove it again, rather
than leaving it on for routine, well-scoped work.
