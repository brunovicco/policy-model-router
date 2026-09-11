"""Framework-free value objects and errors for Governance runtime authorization.

Holds only what the verification rules operate *on*: the stable error type, the trusted key set,
and the pure codec helpers shared by the verifier and by the adapters that load key material. The
signed envelope itself is a Pydantic contract and therefore lives in the application layer - see
``application/runtime_authorization_contract.py``.
"""

import base64
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


class RuntimeAuthorizationKeyStatus(StrEnum):
    """Lifecycle state for one trusted Governance public key."""

    ACTIVE = "active"
    RETIRING = "retiring"
    REVOKED = "revoked"


class RuntimeAuthorizationError(RuntimeError):
    """Fail-closed runtime authorization error with a stable code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        authorization_verified: bool = False,
    ) -> None:
        """Store the stable code and whether cryptographic/binding verification completed."""
        super().__init__(message)
        self.code = code
        self.authorization_verified = authorization_verified


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


def decode_base64url(value: str) -> bytes:
    """Decode unpadded base64url, rejecting any character outside the alphabet."""
    encoded = value.encode("ascii")
    padding = b"=" * ((4 - len(encoded) % 4) % 4)
    return base64.b64decode(encoded + padding, altchars=b"-_", validate=True)


def decode_signature(value: str) -> bytes:
    """Decode a signature and require exactly the 64 bytes Ed25519 produces."""
    decoded = decode_base64url(value)
    if len(decoded) != 64:
        raise ValueError("Ed25519 signature must be 64 bytes")
    return decoded


def parse_utc(value: object) -> datetime:
    """Parse an ISO-8601 string that must already be expressed in UTC."""
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require_utc(parsed)
    return parsed.astimezone(UTC)


def require_utc(value: datetime) -> None:
    """Reject a naive datetime or one carrying a non-zero UTC offset."""
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise RuntimeAuthorizationError(
            "invalid_time",
            "Runtime authorization verification time must be UTC",
        )


__all__ = [
    "RuntimeAuthorizationError",
    "RuntimeAuthorizationKeyStatus",
    "TrustedRuntimeAuthorizationKey",
    "TrustedRuntimeAuthorizationKeySet",
    "decode_base64url",
    "decode_signature",
    "parse_utc",
    "require_utc",
]
