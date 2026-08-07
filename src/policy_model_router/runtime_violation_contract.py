"""Consumer-owned mirror of Governance runtime violation evidence v1."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RUNTIME_VIOLATION_SCHEMA_VERSION: Literal["1.0"] = "1.0"

_Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_Identifier = Annotated[str, Field(min_length=1, max_length=200)]
_Code = Annotated[
    str,
    Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9][a-z0-9_]*$",
    ),
]
_ModelGroup = Annotated[
    str,
    Field(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    ),
]


class RuntimeViolationCategory(StrEnum):
    """Stable bounded classification for machine-actionable violations."""

    AUTHORIZATION = "authorization"
    REPLAY = "replay"
    REQUEST_BINDING = "request_binding"
    GOVERNANCE_PROVENANCE = "governance_provenance"
    MODEL_SCOPE = "model_scope"


class RuntimeViolationAuthorizationState(StrEnum):
    """How far the supplied authorization passed the Router trust boundary."""

    ABSENT = "absent"
    PRESENT = "present"
    VERIFIED = "verified"


class _StrictModel(BaseModel):
    """Immutable closed model for cross-service evidence."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class RuntimeViolationRequestContext(_StrictModel):
    """Content-free request identity sufficient for binding and audit."""

    workflow_id: _Identifier
    task_id: _Identifier
    agent_name: _Identifier
    workload: _Identifier


class RuntimeViolationAuthorizationContext(_StrictModel):
    """Non-secret authorization identifiers observed at enforcement time."""

    state: RuntimeViolationAuthorizationState
    authorization_id: UUID | None = None
    key_id: _Identifier | None = None
    signing_digest: _Digest | None = None
    scope_digest: _Digest | None = None

    @model_validator(mode="after")
    def validate_state(self) -> "RuntimeViolationAuthorizationContext":
        """Require structural identifiers when an authorization was present."""
        identifiers = (
            self.authorization_id,
            self.key_id,
            self.signing_digest,
            self.scope_digest,
        )
        if self.state is RuntimeViolationAuthorizationState.ABSENT:
            if any(value is not None for value in identifiers):
                raise ValueError("Absent authorization cannot carry authorization identifiers")
            return self
        if any(value is None for value in identifiers):
            raise ValueError("Present authorization must carry all structural identifiers")
        return self


class RuntimeViolationEvent(_StrictModel):
    """One Router-side enforcement denial with no prompt, output, or credential content."""

    schema_version: Literal["1.0"] = RUNTIME_VIOLATION_SCHEMA_VERSION
    event_id: UUID
    occurred_at: datetime
    source_service: Literal["policy-model-router"] = "policy-model-router"
    service_version: _Identifier
    environment: _Identifier
    correlation_id: _Identifier
    category: RuntimeViolationCategory
    code: _Code
    enforcement_action: Literal["blocked"] = "blocked"
    request: RuntimeViolationRequestContext
    authorization: RuntimeViolationAuthorizationContext
    selected_model_group: _ModelGroup | None = None

    @field_validator("occurred_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        """Require explicit UTC to make evidence ordering unambiguous."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Runtime violation timestamp must be timezone-aware")
        if value.utcoffset() != timedelta(0):
            raise ValueError("Runtime violation timestamp must be expressed in UTC")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def validate_semantics(self) -> "RuntimeViolationEvent":
        """Reject internally inconsistent category and trust-state combinations."""
        if (
            self.category is not RuntimeViolationCategory.AUTHORIZATION
            and self.authorization.state is RuntimeViolationAuthorizationState.ABSENT
        ):
            raise ValueError("This violation category requires an authorization envelope")
        if self.category is RuntimeViolationCategory.MODEL_SCOPE:
            if self.authorization.state is not RuntimeViolationAuthorizationState.VERIFIED:
                raise ValueError("Model-scope violations require verified authorization")
            if self.selected_model_group is None:
                raise ValueError("Model-scope violations require the selected model group")
        elif self.selected_model_group is not None:
            raise ValueError("Selected model group is only valid for model-scope violations")
        return self

    def canonical_bytes(self) -> bytes:
        """Return deterministic UTF-8 bytes used for the evidence digest."""
        return _canonical_json_bytes(self.model_dump(mode="json"))

    def digest(self) -> str:
        """Return the SHA-256 digest of the event payload."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


class RuntimeViolationEnvelope(_StrictModel):
    """Event plus self-integrity digest consumed and persisted by Governance."""

    event: RuntimeViolationEvent
    event_digest: _Digest

    @model_validator(mode="after")
    def verify_digest(self) -> "RuntimeViolationEnvelope":
        """Reject an event whose supplied digest does not match its canonical payload."""
        if self.event.digest() != self.event_digest:
            raise ValueError("Runtime violation digest does not match the event")
        return self

    @classmethod
    def from_event(cls, event: RuntimeViolationEvent) -> "RuntimeViolationEnvelope":
        """Create a valid envelope from one event."""
        return cls(event=event, event_digest=event.digest())


def _canonical_json_bytes(value: object) -> bytes:
    """Serialize the constrained contract deterministically."""
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Runtime violation is not canonically serializable") from exc
    return text.encode("utf-8")
