# syntax=docker/dockerfile:1
#
# Build from the committed lock file and keep uv and build tools out of the runtime image.

FROM python:3.13-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91 AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.28@sha256:0f36cb9361a3346885ca3677e3767016687b5a170c1a6b88465ec14aefec90aa /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Cache dependencies independently from source changes.
# --extra rate-limit ships the redis client so REDIS_URL (ADR-0008) works in the built image.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev --extra rate-limit

COPY . /app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --extra rate-limit

FROM python:3.13-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91

RUN groupadd --system app && useradd --system --gid app --no-create-home app

WORKDIR /app

COPY --from=builder --chown=app:app /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app

# /health is unauthenticated and unthrottled by design (ADR-0007), so an orchestrator can probe it
# cheaply. Python rather than curl: the slim base ships neither curl nor wget.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"]

# TRUSTED_PROXY_IPS is unset by default, so the rate-limit key keeps using the raw TCP peer address
# and no forwarded header is trusted from anyone. Set it to the proxy's own address (never "*") to
# recover per-client granularity behind an ingress - see the README's rate-limiting section for why
# trusting the header from an unrestricted set of peers lets any client multiply its quota.
CMD ["sh", "-c", "exec uvicorn policy_model_router.entrypoints.http:app --host 0.0.0.0 --port 8000 ${TRUSTED_PROXY_IPS:+--proxy-headers --forwarded-allow-ips \"$TRUSTED_PROXY_IPS\"}"]
