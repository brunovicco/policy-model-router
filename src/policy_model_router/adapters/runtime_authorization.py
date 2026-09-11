"""Adapters for runtime authorization: replay consumption and trusted key material.

The replay guards implement the application's :class:`RuntimeAuthorizationReplayGuard` port. Redis
is imported by the composition root, not here, so this module stays importable - and its logic
testable against a fake client - without the optional ``rate-limit`` extra installed.
"""

import asyncio
import binascii
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from policy_model_router.domain.runtime_authorization import (
    RuntimeAuthorizationError,
    RuntimeAuthorizationKeyStatus,
    TrustedRuntimeAuthorizationKey,
    TrustedRuntimeAuthorizationKeySet,
    decode_base64url,
    parse_utc,
)


class InMemoryRuntimeAuthorizationReplayGuard:
    """Bounded process-local replay guard for development and tests."""

    def __init__(self, *, max_entries: int = 10_000) -> None:
        """Bound the guard to at most ``max_entries`` live authorization IDs."""
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self._max_entries = max_entries
        self._entries: dict[UUID, datetime] = {}
        self._lock = asyncio.Lock()

    async def consume(
        self,
        authorization_id: UUID,
        *,
        expires_at: datetime,
        now: datetime,
    ) -> bool:
        """Consume one ID exactly once while it remains live."""
        async with self._lock:
            self._entries = {key: expiry for key, expiry in self._entries.items() if expiry > now}
            if authorization_id in self._entries:
                return False
            if len(self._entries) >= self._max_entries:
                raise RuntimeAuthorizationError(
                    "replay_store_full",
                    "Runtime authorization replay store is at capacity",
                )
            self._entries[authorization_id] = expires_at
            return True

    async def ping(self) -> None:
        """In-memory state is always reachable."""
        return None

    async def close(self) -> None:
        """No resources to release."""
        return None


class RedisRuntimeAuthorizationReplayGuard:
    """Cross-replica replay guard using atomic Redis SET NX EX."""

    def __init__(self, client: Any, *, key_prefix: str) -> None:
        """Wrap an async Redis ``client``, namespacing keys with ``key_prefix``."""
        if not key_prefix:
            raise ValueError("Replay key prefix must not be empty")
        self._client = client
        self._key_prefix = key_prefix

    async def consume(
        self,
        authorization_id: UUID,
        *,
        expires_at: datetime,
        now: datetime,
    ) -> bool:
        """Atomically consume the authorization ID until it expires."""
        ttl = max(1, math.ceil((expires_at - now).total_seconds()))
        key = f"{self._key_prefix}{authorization_id}"
        try:
            result = await self._client.set(key, "1", nx=True, ex=ttl)
        except Exception as exc:
            raise RuntimeAuthorizationError(
                "replay_store_unavailable",
                "Runtime authorization replay state is unavailable",
            ) from exc
        return bool(result)

    async def ping(self) -> None:
        """Require the Redis backend to be reachable at startup."""
        try:
            await self._client.ping()
        except Exception as exc:
            raise RuntimeAuthorizationError(
                "replay_store_unavailable",
                "Runtime authorization replay state is unavailable",
            ) from exc

    async def close(self) -> None:
        """Close the owned Redis client."""
        close = getattr(self._client, "aclose", None)
        if close is not None:
            await close()


def load_trusted_key_set(
    path: Path,
    *,
    max_bytes: int = 262_144,
) -> TrustedRuntimeAuthorizationKeySet:
    """Load a bounded public-only key set emitted by Governance."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RuntimeAuthorizationError(
            "key_set_unavailable",
            "Runtime authorization key set could not be loaded",
        ) from exc
    if len(raw) > max_bytes:
        raise RuntimeAuthorizationError(
            "key_set_too_large",
            "Runtime authorization key set exceeds the configured limit",
        )
    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeAuthorizationError(
            "invalid_key_set",
            "Runtime authorization key set is invalid",
        ) from exc
    return _parse_key_set(document)


def _parse_key_set(document: object) -> TrustedRuntimeAuthorizationKeySet:
    if not isinstance(document, dict):
        raise RuntimeAuthorizationError("invalid_key_set", "Key set must be an object")
    if set(document) != {"schema_version", "generation", "keys"}:
        raise RuntimeAuthorizationError(
            "invalid_key_set",
            "Key set contains unsupported fields",
        )
    if document["schema_version"] != "1.0":
        raise RuntimeAuthorizationError(
            "invalid_key_set",
            "Unsupported key-set schema version",
        )
    generation = document["generation"]
    entries = document["keys"]
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise RuntimeAuthorizationError("invalid_key_set", "Invalid key-set generation")
    if not isinstance(entries, list) or not entries:
        raise RuntimeAuthorizationError("invalid_key_set", "Key set must contain keys")

    keys: list[TrustedRuntimeAuthorizationKey] = []
    seen: set[str] = set()
    for entry in entries:
        key = _parse_key_entry(entry)
        if key.kid in seen:
            raise RuntimeAuthorizationError("invalid_key_set", "Duplicate key identifier")
        seen.add(key.kid)
        keys.append(key)
    if not any(key.status is RuntimeAuthorizationKeyStatus.ACTIVE for key in keys):
        raise RuntimeAuthorizationError(
            "invalid_key_set",
            "Key set must contain at least one active key",
        )
    return TrustedRuntimeAuthorizationKeySet(
        generation=generation,
        keys=tuple(keys),
    )


def _parse_key_entry(entry: object) -> TrustedRuntimeAuthorizationKey:
    if not isinstance(entry, dict):
        raise RuntimeAuthorizationError("invalid_key_set", "Key entry must be an object")
    expected = {"kid", "status", "not_before", "verify_until", "jwk"}
    if set(entry) != expected:
        raise RuntimeAuthorizationError(
            "invalid_key_set",
            "Key entry contains unsupported fields",
        )
    kid = entry["kid"]
    status = entry["status"]
    jwk = entry["jwk"]
    if not isinstance(kid, str) or not kid:
        raise RuntimeAuthorizationError("invalid_key_set", "Invalid key identifier")
    try:
        key_status = RuntimeAuthorizationKeyStatus(status)
    except (TypeError, ValueError) as exc:
        raise RuntimeAuthorizationError("invalid_key_set", "Invalid key status") from exc
    if not isinstance(jwk, dict) or set(jwk) != {"kty", "crv", "x"}:
        raise RuntimeAuthorizationError("invalid_key_set", "Invalid Ed25519 JWK")
    if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
        raise RuntimeAuthorizationError(
            "invalid_key_set",
            "Only Ed25519 OKP keys are trusted",
        )
    x = jwk.get("x")
    if not isinstance(x, str):
        raise RuntimeAuthorizationError("invalid_key_set", "Missing Ed25519 public key")
    try:
        public_bytes = decode_base64url(x)
        public_key = Ed25519PublicKey.from_public_bytes(public_bytes)
        not_before = parse_utc(entry["not_before"])
        verify_until = parse_utc(entry["verify_until"])
    except RuntimeAuthorizationError as exc:
        # `parse_utc` rejects a non-UTC timestamp with `invalid_time`, whose message is about the
        # service's own verification clock. Reported against a key file it would send an operator
        # to inspect the wrong thing, so it is normalized here: everything wrong with key material
        # is `invalid_key_set`.
        raise RuntimeAuthorizationError(
            "invalid_key_set",
            "Runtime authorization key material is invalid",
        ) from exc
    except (ValueError, TypeError, binascii.Error) as exc:
        raise RuntimeAuthorizationError(
            "invalid_key_set",
            "Runtime authorization key material is invalid",
        ) from exc
    if len(public_bytes) != 32 or verify_until <= not_before:
        raise RuntimeAuthorizationError(
            "invalid_key_set",
            "Runtime authorization key window or size is invalid",
        )
    return TrustedRuntimeAuthorizationKey(
        kid=kid,
        status=key_status,
        public_key=public_key,
        not_before=not_before,
        verify_until=verify_until,
    )


def parse_agent_bindings(raw_json: str) -> dict[str, UUID]:
    """Parse exact agent-name to Governance UUID bindings."""
    try:
        raw = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise RuntimeAuthorizationError(
            "invalid_agent_bindings",
            "Runtime authorization agent bindings must be valid JSON",
        ) from exc
    if not isinstance(raw, dict) or not raw:
        raise RuntimeAuthorizationError(
            "invalid_agent_bindings",
            "Runtime authorization agent bindings must be a non-empty object",
        )
    bindings: dict[str, UUID] = {}
    for agent_name, agent_id in raw.items():
        if (
            not isinstance(agent_name, str)
            or not agent_name
            or len(agent_name) > 200
            or not isinstance(agent_id, str)
        ):
            raise RuntimeAuthorizationError(
                "invalid_agent_bindings",
                "Runtime authorization agent bindings are invalid",
            )
        try:
            parsed = UUID(agent_id)
        except ValueError as exc:
            raise RuntimeAuthorizationError(
                "invalid_agent_bindings",
                "Runtime authorization agent binding must contain UUIDs",
            ) from exc
        bindings[agent_name] = parsed
    return bindings


__all__ = [
    "InMemoryRuntimeAuthorizationReplayGuard",
    "RedisRuntimeAuthorizationReplayGuard",
    "load_trusted_key_set",
    "parse_agent_bindings",
]
