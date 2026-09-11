"""Consumer-owned mirror of Governance runtime authorization v1.

This module deliberately mirrors the signed contract instead of importing the Governance
repository. Canonical signing bytes must remain byte-for-byte compatible across repositories.
"""

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from policy_model_router.domain.enums import DataClassification

RUNTIME_AUTHORIZATION_SCHEMA_VERSION: Literal["1.0"] = "1.0"
RUNTIME_AUTHORIZATION_MEDIA_TYPE: Literal[
    "application/vnd.verifiable-ai-governance.runtime-authorization+json"
] = "application/vnd.verifiable-ai-governance.runtime-authorization+json"
RUNTIME_AUTHORIZATION_SIGNATURE_ALGORITHM: Literal["Ed25519"] = "Ed25519"
MAX_AUTHORIZATION_LIFETIME_SECONDS = 600

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Identifier = Annotated[
    str,
    Field(
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    ),
]
RoutingGroup = Annotated[
    str,
    Field(
        min_length=2,
        max_length=100,
        pattern=r"^[a-z0-9][a-z0-9._-]*$",
    ),
]
ShortText = Annotated[str, Field(min_length=1, max_length=300)]


class AuthorizationRiskTier(StrEnum):
    """Risk vocabulary mirrored from Governance."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AuthorizationAutonomyLevel(StrEnum):
    """Agent autonomy vocabulary mirrored from Governance."""

    A0_INFORMATION = "a0_information"
    A1_RECOMMENDATION = "a1_recommendation"
    A2_PREPARE_FOR_APPROVAL = "a2_prepare_for_approval"
    A3_REVERSIBLE_ACTIONS = "a3_reversible_actions"
    A4_HIGH_IMPACT_ACTIONS = "a4_high_impact_actions"
    A5_HIGH_AUTONOMY = "a5_high_autonomy"


class ContractModel(BaseModel):
    """Strict immutable base model for authorization contract records."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class RuntimeAuthorizationProtectedHeader(ContractModel):
    """Metadata covered by the signature together with the claims."""

    typ: Literal["application/vnd.verifiable-ai-governance.runtime-authorization+json"] = (
        RUNTIME_AUTHORIZATION_MEDIA_TYPE
    )
    alg: Literal["Ed25519"] = RUNTIME_AUTHORIZATION_SIGNATURE_ALGORITHM
    kid: Identifier


class RuntimeAuthorizationPolicyProvenance(ContractModel):
    """Exact Governance artifacts that produced runtime authorization."""

    policy_id: Identifier
    policy_version: Identifier
    policy_digest: Digest
    control_catalog_id: Identifier
    control_catalog_version: Identifier
    control_catalog_digest: Digest


class RuntimeAuthorizationSubject(ContractModel):
    """Governed subject whose approved runtime scope is authorized."""

    initiative_id: UUID
    ai_system_id: UUID
    ai_system_version: int = Field(ge=1)
    agent_id: UUID
    agent_version: int = Field(ge=1)
    agent_review_digest: Digest


class AuthorizedRuntimeModel(ContractModel):
    """One reviewed model that may satisfy this authorization."""

    model_id: UUID
    entity_version: int = Field(ge=1)
    model_version: Identifier
    routing_group: RoutingGroup
    review_digest: Digest
    allowed_data_classes: tuple[DataClassification, ...] = Field(min_length=1)

    @field_validator("allowed_data_classes")
    @classmethod
    def normalize_data_classes(
        cls,
        values: tuple[DataClassification, ...],
    ) -> tuple[DataClassification, ...]:
        """Sort and deduplicate data classifications."""
        return tuple(sorted(set(values), key=lambda value: value.value))


class RuntimeRequestBinding(ContractModel):
    """Exact request facts that prevent authorization reuse across tasks."""

    workflow_id: Identifier
    task_id: Identifier
    workload: Identifier
    context_tokens_estimated: int = Field(ge=0, le=10_000_000)
    max_output_tokens_estimated: int = Field(ge=0, le=1_000_000)
    structured_output_required: bool
    max_latency_ms: int = Field(ge=1, le=3_600_000)
    max_cost_usd_micros: int = Field(ge=0, le=1_000_000_000_000)


class RuntimeAuthorizationScope(ContractModel):
    """Effective machine-enforceable boundary approved for one request."""

    risk_tier: AuthorizationRiskTier
    data_classification: DataClassification
    autonomy_level: AuthorizationAutonomyLevel
    models: tuple[AuthorizedRuntimeModel, ...] = Field(min_length=1)
    allowed_tools: tuple[Identifier, ...] = ()
    permissions: tuple[Identifier, ...] = ()
    max_runtime_seconds: int = Field(ge=1, le=86_400)
    human_approval_points: tuple[ShortText, ...] = ()
    kill_switch_enabled: Literal[True] = True

    @field_validator("allowed_tools", "permissions", "human_approval_points")
    @classmethod
    def normalize_strings(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Sort and deduplicate set-like collections."""
        return tuple(sorted(set(values)))

    @field_validator("models")
    @classmethod
    def normalize_models(
        cls,
        values: tuple[AuthorizedRuntimeModel, ...],
    ) -> tuple[AuthorizedRuntimeModel, ...]:
        """Sort models and reject duplicate model IDs."""
        model_ids = [str(model.model_id) for model in values]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("Runtime authorization cannot contain duplicate model IDs")
        return tuple(sorted(values, key=lambda model: str(model.model_id)))

    @model_validator(mode="after")
    def validate_capability_boundary(self) -> "RuntimeAuthorizationScope":
        """Enforce cross-field safety invariants."""
        if self.allowed_tools and not self.permissions:
            raise ValueError("Runtime authorization tools require an explicit permission boundary")
        if (
            self.autonomy_level
            in {
                AuthorizationAutonomyLevel.A2_PREPARE_FOR_APPROVAL,
                AuthorizationAutonomyLevel.A3_REVERSIBLE_ACTIONS,
                AuthorizationAutonomyLevel.A4_HIGH_IMPACT_ACTIONS,
                AuthorizationAutonomyLevel.A5_HIGH_AUTONOMY,
            }
            and not self.human_approval_points
        ):
            raise ValueError("Material runtime autonomy requires a human approval point")
        if not any(self.data_classification in model.allowed_data_classes for model in self.models):
            raise ValueError("At least one authorized model must allow the runtime data class")
        return self


class RuntimeAuthorizationClaims(ContractModel):
    """Short-lived claims emitted by Governance for one runtime request."""

    schema_version: Literal["1.0"] = RUNTIME_AUTHORIZATION_SCHEMA_VERSION
    authorization_id: UUID
    issuer: Identifier
    audience: tuple[Identifier, ...] = Field(min_length=1)
    issued_at: datetime
    not_before: datetime
    expires_at: datetime
    subject: RuntimeAuthorizationSubject
    request: RuntimeRequestBinding
    scope: RuntimeAuthorizationScope
    scope_digest: Digest
    policy: RuntimeAuthorizationPolicyProvenance

    @field_validator("audience")
    @classmethod
    def normalize_audience(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        """Sort and deduplicate intended consumers."""
        return tuple(sorted(set(values)))

    @field_validator("issued_at", "not_before", "expires_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        """Require explicit UTC."""
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Runtime authorization timestamps must be timezone-aware")
        normalized = value.astimezone(UTC)
        if value.utcoffset() != timedelta(0):
            raise ValueError("Runtime authorization timestamps must be expressed in UTC")
        return normalized

    @model_validator(mode="after")
    def validate_time_window(self) -> "RuntimeAuthorizationClaims":
        """Reject inverted or excessive authorization windows."""
        if self.not_before < self.issued_at:
            raise ValueError("not_before cannot precede issued_at")
        if self.expires_at <= self.not_before:
            raise ValueError("expires_at must follow not_before")
        if self.expires_at - self.issued_at > timedelta(seconds=MAX_AUTHORIZATION_LIFETIME_SECONDS):
            raise ValueError("Runtime authorization lifetime exceeds the v1 maximum")
        return self


class SignedRuntimeAuthorization(ContractModel):
    """Signed wire envelope consumed by the Router."""

    protected: RuntimeAuthorizationProtectedHeader
    claims: RuntimeAuthorizationClaims
    signature: str = Field(
        min_length=86,
        max_length=86,
        pattern=r"^[A-Za-z0-9_-]{86}$",
    )

    @field_validator("signature")
    @classmethod
    def validate_signature_encoding(cls, value: str) -> str:
        """Require unpadded base64url encoding of exactly 64 signature bytes."""
        try:
            decoded = base64.urlsafe_b64decode(value + "==")
        except (ValueError, UnicodeError) as exc:
            raise ValueError("signature must be valid base64url") from exc
        if len(decoded) != 64:
            raise ValueError("Ed25519 signature must decode to exactly 64 bytes")
        return value

    def signing_bytes(self) -> bytes:
        """Return exactly the bytes Governance signs."""
        return canonical_json_bytes(
            {
                "protected": self.protected.model_dump(mode="json"),
                "claims": self.claims.model_dump(mode="json"),
            }
        )

    def signing_digest(self) -> str:
        """Return SHA-256 of the protected bytes."""
        return hashlib.sha256(self.signing_bytes()).hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    """Serialize the constrained v1 contract using canonical UTF-8 JSON."""
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("Runtime authorization value is not canonically serializable") from exc
    return text.encode("utf-8")
