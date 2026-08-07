"""Verify Governance runtime authorization before deterministic model routing."""

import asyncio
import base64
import binascii
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from policy_model_router.domain.enums import ModelGroup
from policy_model_router.entrypoints.contracts import ModelRouteRequest
from policy_model_router.runtime_authorization_contract import (
    RuntimeAuthorizationClaims,
    SignedRuntimeAuthorization,
)


class RuntimeAuthorizationKeyStatus(StrEnum):
    """Lifecycle state for one trusted Governance public key."""

    ACTIVE = "active"
    RETIRING = "retiring"
    REVOKED = "revoked"


class RuntimeAuthorizationError(RuntimeError):
    """Fail-closed runtime authorization error with a stable code."""

    def __init__(self, code: str, message: str) -> None:
        """Store the stable machine-readable ``code`` alongside the human ``message``."""
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TrustedRuntimeAuthorizationKey:
    """One trusted Ed25519 verification key."""

    kid: str
    status: RuntimeAuthorizationKeyStatus
    public_key: Ed25519PublicKey
    not_before: datetime
    verify_until: datetime


@dataclass(frozen=True, slots=True)
class TrustedRuntimeAuthorizationKeySet:
    """Versioned exact-key trust set."""

    generation: int
    keys: tuple[TrustedRuntimeAuthorizationKey, ...]

    def resolve(self, kid: str) -> TrustedRuntimeAuthorizationKey | None:
        """Resolve exactly one key ID without fallback."""
        return next((key for key in self.keys if key.kid == kid), None)


@dataclass(frozen=True, slots=True)
class VerifiedRuntimeAuthorization:
    """Cryptographically verified artifact bound to one route request."""

    envelope: SignedRuntimeAuthorization
    verified_at: datetime
    key_set_generation: int

    @property
    def authorization_id(self) -> UUID:
        """Return the single-use authorization identifier."""
        return self.envelope.claims.authorization_id


class RuntimeAuthorizationReplayGuard(Protocol):
    """Atomic replay-consumption boundary."""

    async def consume(
        self,
        authorization_id: UUID,
        *,
        expires_at: datetime,
        now: datetime,
    ) -> bool:
        """Return whether the ID was newly consumed."""
        ...

    async def ping(self) -> None:
        """Raise when replay state is unavailable."""
        ...

    async def close(self) -> None:
        """Release resources."""
        ...


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


class RuntimeAuthorizationVerifier:
    """Verify signature, request binding, agent binding and one-time use."""

    def __init__(
        self,
        *,
        key_set: TrustedRuntimeAuthorizationKeySet,
        replay_guard: RuntimeAuthorizationReplayGuard,
        issuer: str,
        audience: str,
        agent_bindings: dict[str, UUID],
        expected_policy_id: str,
        expected_policy_version: str,
        expected_policy_digest: str,
        expected_control_catalog_id: str,
        expected_control_catalog_version: str,
        expected_control_catalog_digest: str,
        clock_skew_seconds: int = 0,
    ) -> None:
        """Bind the trust set, replay guard, and expected Governance provenance to verify."""
        if not issuer or not audience:
            raise ValueError("Runtime authorization issuer and audience are required")
        if not 0 <= clock_skew_seconds <= 60:
            raise ValueError("clock_skew_seconds must be between 0 and 60")
        if not agent_bindings:
            raise ValueError("At least one runtime authorization agent binding is required")
        self._key_set = key_set
        self._replay_guard = replay_guard
        self._issuer = issuer
        self._audience = audience
        self._agent_bindings = dict(agent_bindings)
        self._expected_policy_id = expected_policy_id
        self._expected_policy_version = expected_policy_version
        self._expected_policy_digest = expected_policy_digest
        self._expected_control_catalog_id = expected_control_catalog_id
        self._expected_control_catalog_version = expected_control_catalog_version
        self._expected_control_catalog_digest = expected_control_catalog_digest
        self._clock_skew = timedelta(seconds=clock_skew_seconds)

    async def verify(
        self,
        envelope: SignedRuntimeAuthorization,
        request: ModelRouteRequest,
        *,
        now: datetime,
    ) -> VerifiedRuntimeAuthorization:
        """Verify and atomically consume one authorization."""
        _require_utc(now)
        claims = envelope.claims
        self._verify_identity_and_time(claims, now)
        key = self._require_key(envelope, now)
        self._verify_signature(envelope, key)
        self._verify_request_binding(claims, request)
        self._verify_agent_binding(claims, request.agent_name)
        self._verify_policy_provenance(claims)

        try:
            fresh = await self._replay_guard.consume(
                claims.authorization_id,
                expires_at=claims.expires_at,
                now=now,
            )
        except RuntimeAuthorizationError:
            raise
        except Exception as exc:
            raise RuntimeAuthorizationError(
                "replay_store_unavailable",
                "Runtime authorization replay state is unavailable",
            ) from exc
        if not fresh:
            raise RuntimeAuthorizationError(
                "replay_detected",
                "Runtime authorization identifier was already consumed",
            )
        return VerifiedRuntimeAuthorization(
            envelope=envelope,
            verified_at=now,
            key_set_generation=self._key_set.generation,
        )

    def require_selected_model(
        self,
        verified: VerifiedRuntimeAuthorization,
        selected_model_group: ModelGroup,
    ) -> None:
        """Require the Router-selected group to be explicitly signed by Governance."""
        claims = verified.envelope.claims
        matching = tuple(
            model
            for model in claims.scope.models
            if model.routing_group == selected_model_group.value
        )
        if not matching:
            raise RuntimeAuthorizationError(
                "selected_model_group_not_authorized",
                "Selected model group is outside the signed Governance scope",
            )
        if not any(
            claims.scope.data_classification in model.allowed_data_classes for model in matching
        ):
            raise RuntimeAuthorizationError(
                "selected_model_data_class_not_authorized",
                "Selected model group is not signed for this data classification",
            )

    async def ping(self) -> None:
        """Require replay state to be reachable."""
        await self._replay_guard.ping()

    async def close(self) -> None:
        """Release replay resources."""
        await self._replay_guard.close()

    def _verify_identity_and_time(
        self,
        claims: RuntimeAuthorizationClaims,
        now: datetime,
    ) -> None:
        if claims.issuer != self._issuer:
            raise RuntimeAuthorizationError(
                "issuer_mismatch",
                "Runtime authorization issuer is not trusted",
            )
        if self._audience not in claims.audience:
            raise RuntimeAuthorizationError(
                "audience_mismatch",
                "Runtime authorization audience does not include this service",
            )
        if claims.issued_at > now + self._clock_skew:
            raise RuntimeAuthorizationError(
                "issued_in_future",
                "Runtime authorization was issued in the future",
            )
        if claims.not_before > now + self._clock_skew:
            raise RuntimeAuthorizationError(
                "not_yet_valid",
                "Runtime authorization is not valid yet",
            )
        if claims.expires_at <= now - self._clock_skew:
            raise RuntimeAuthorizationError(
                "expired",
                "Runtime authorization has expired",
            )

    def _require_key(
        self,
        envelope: SignedRuntimeAuthorization,
        now: datetime,
    ) -> TrustedRuntimeAuthorizationKey:
        key = self._key_set.resolve(envelope.protected.kid)
        if key is None:
            raise RuntimeAuthorizationError(
                "unknown_key",
                "Runtime authorization key is not trusted",
            )
        if key.status is RuntimeAuthorizationKeyStatus.REVOKED:
            raise RuntimeAuthorizationError(
                "key_revoked",
                "Runtime authorization key is revoked",
            )
        if not key.not_before <= envelope.claims.issued_at < key.verify_until:
            raise RuntimeAuthorizationError(
                "key_not_valid_for_issue_time",
                "Authorization issue time is outside the key trust window",
            )
        if now - self._clock_skew >= key.verify_until:
            raise RuntimeAuthorizationError(
                "key_verification_window_closed",
                "Runtime authorization key verification window is closed",
            )
        return key

    @staticmethod
    def _verify_signature(
        envelope: SignedRuntimeAuthorization,
        key: TrustedRuntimeAuthorizationKey,
    ) -> None:
        try:
            signature = _decode_signature(envelope.signature)
            key.public_key.verify(signature, envelope.signing_bytes())
        except (InvalidSignature, ValueError, binascii.Error) as exc:
            raise RuntimeAuthorizationError(
                "invalid_signature",
                "Runtime authorization signature is invalid",
            ) from exc

    @staticmethod
    def _verify_request_binding(
        claims: RuntimeAuthorizationClaims,
        request: ModelRouteRequest,
    ) -> None:
        signed = claims.request
        cost_micros = _cost_micros(request.max_cost_usd)
        mismatches = {
            "requested_at": claims.issued_at != request.requested_at,
            "workflow_id": signed.workflow_id != request.workflow_id,
            "task_id": signed.task_id != request.task_id,
            "workload": signed.workload != request.workload.value,
            "context_tokens_estimated": (
                signed.context_tokens_estimated != request.context_tokens_estimated
            ),
            "max_output_tokens_estimated": (
                signed.max_output_tokens_estimated != request.max_output_tokens_estimated
            ),
            "structured_output_required": (
                signed.structured_output_required != request.structured_output_required
            ),
            "max_latency_ms": signed.max_latency_ms != request.max_latency_ms,
            "max_cost_usd_micros": signed.max_cost_usd_micros != cost_micros,
            "risk_level": claims.scope.risk_tier.value != request.risk_level.value,
            "data_classification": (
                claims.scope.data_classification != request.data_classification
            ),
        }
        failed = tuple(sorted(name for name, mismatch in mismatches.items() if mismatch))
        if failed:
            raise RuntimeAuthorizationError(
                "request_binding_mismatch",
                "Route request differs from signed Governance authorization: " + ", ".join(failed),
            )

    def _verify_agent_binding(
        self,
        claims: RuntimeAuthorizationClaims,
        agent_name: str,
    ) -> None:
        expected_agent_id = self._agent_bindings.get(agent_name)
        if expected_agent_id is None or expected_agent_id != claims.subject.agent_id:
            raise RuntimeAuthorizationError(
                "agent_binding_mismatch",
                "Route agent identity does not match signed Governance subject",
            )

    def _verify_policy_provenance(
        self,
        claims: RuntimeAuthorizationClaims,
    ) -> None:
        policy = claims.policy
        if (
            policy.policy_id != self._expected_policy_id
            or policy.policy_version != self._expected_policy_version
            or policy.policy_digest != self._expected_policy_digest
            or policy.control_catalog_id != self._expected_control_catalog_id
            or policy.control_catalog_version != self._expected_control_catalog_version
            or policy.control_catalog_digest != self._expected_control_catalog_digest
        ):
            raise RuntimeAuthorizationError(
                "governance_policy_mismatch",
                "Runtime authorization provenance is not trusted by this deployment",
            )


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
        public_bytes = _decode_base64url(x)
        public_key = Ed25519PublicKey.from_public_bytes(public_bytes)
        not_before = _parse_utc(entry["not_before"])
        verify_until = _parse_utc(entry["verify_until"])
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


def _decode_signature(value: str) -> bytes:
    decoded = _decode_base64url(value)
    if len(decoded) != 64:
        raise ValueError("Ed25519 signature must be 64 bytes")
    return decoded


def _decode_base64url(value: str) -> bytes:
    encoded = value.encode("ascii")
    padding = b"=" * ((4 - len(encoded) % 4) % 4)
    return base64.b64decode(encoded + padding, altchars=b"-_", validate=True)


def _parse_utc(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    _require_utc(parsed)
    return parsed.astimezone(UTC)


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise RuntimeAuthorizationError(
            "invalid_time",
            "Runtime authorization verification time must be UTC",
        )


def _cost_micros(value: Decimal) -> int:
    micros = value * Decimal(1_000_000)
    integral = micros.to_integral_value()
    if micros != integral:
        raise RuntimeAuthorizationError(
            "request_cost_precision_unsupported",
            "Route cost cannot be represented as integer USD micros",
        )
    return int(integral)
