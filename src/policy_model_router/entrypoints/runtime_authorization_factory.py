"""Composition helpers for runtime authorization verification."""

from typing import Any

from policy_model_router.entrypoints.runtime_authorization_settings import (
    RuntimeAuthorizationSettings,
)
from policy_model_router.runtime_authorization import (
    InMemoryRuntimeAuthorizationReplayGuard,
    RedisRuntimeAuthorizationReplayGuard,
    RuntimeAuthorizationVerifier,
    load_trusted_key_set,
    parse_agent_bindings,
)


class DisabledRuntimeAuthorizationVerifier:
    """Development-only no-op boundary used when authorization is explicitly disabled."""

    async def ping(self) -> None:
        """No dependency to probe."""
        return None

    async def close(self) -> None:
        """No dependency to release."""
        return None


def build_runtime_authorization_verifier(
    settings: RuntimeAuthorizationSettings,
    *,
    app_env: str,
    redis_url: str | None,
) -> RuntimeAuthorizationVerifier | DisabledRuntimeAuthorizationVerifier:
    """Build the configured verifier, failing closed for deployed environments."""
    deployed = app_env in {"staging", "production"}
    if deployed and not settings.required:
        raise RuntimeError("RUNTIME_AUTHORIZATION_REQUIRED must be true in staging and production")
    if not settings.required:
        return DisabledRuntimeAuthorizationVerifier()

    if settings.trusted_key_set_path is None:
        raise RuntimeError("RUNTIME_AUTHORIZATION_TRUSTED_KEY_SET_PATH is required")
    _require_digest(
        "RUNTIME_AUTHORIZATION_EXPECTED_POLICY_DIGEST",
        settings.expected_policy_digest,
    )
    _require_digest(
        "RUNTIME_AUTHORIZATION_EXPECTED_CONTROL_CATALOG_DIGEST",
        settings.expected_control_catalog_digest,
    )
    key_set = load_trusted_key_set(
        settings.trusted_key_set_path,
        max_bytes=settings.max_key_set_bytes,
    )
    bindings = parse_agent_bindings(settings.agent_bindings_json)
    replay_guard = _build_replay_guard(
        app_env=app_env,
        redis_url=redis_url,
        key_prefix=settings.replay_key_prefix,
        max_entries=settings.replay_max_entries,
    )
    return RuntimeAuthorizationVerifier(
        key_set=key_set,
        replay_guard=replay_guard,
        issuer=settings.issuer,
        audience=settings.audience,
        agent_bindings=bindings,
        expected_policy_id=settings.expected_policy_id,
        expected_policy_version=settings.expected_policy_version,
        expected_policy_digest=settings.expected_policy_digest,
        expected_control_catalog_id=settings.expected_control_catalog_id,
        expected_control_catalog_version=settings.expected_control_catalog_version,
        expected_control_catalog_digest=settings.expected_control_catalog_digest,
        clock_skew_seconds=settings.clock_skew_seconds,
    )


def _require_digest(name: str, value: str) -> None:
    """Require an exact lowercase SHA-256 deployment trust value."""
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise RuntimeError(f"{name} must be a lowercase SHA-256 digest")


def _build_replay_guard(
    *,
    app_env: str,
    redis_url: str | None,
    key_prefix: str,
    max_entries: int,
) -> Any:
    """Use Redis for deployed/shared verification and memory only for local/test."""
    if redis_url:
        try:
            from redis.asyncio import Redis
        except ImportError as exc:
            raise RuntimeError(
                "REDIS_URL is set but redis is not installed; "
                "install with `uv sync --extra rate-limit`"
            ) from exc
        client = Redis.from_url(
            redis_url,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
        )
        return RedisRuntimeAuthorizationReplayGuard(
            client,
            key_prefix=key_prefix,
        )
    if app_env in {"staging", "production"}:
        raise RuntimeError(
            "REDIS_URL is required for distributed runtime authorization replay protection"
        )
    return InMemoryRuntimeAuthorizationReplayGuard(max_entries=max_entries)
