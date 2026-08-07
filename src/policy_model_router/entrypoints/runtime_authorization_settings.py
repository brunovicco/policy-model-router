"""Runtime authorization settings for the Router trust boundary."""

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeAuthorizationSettings(BaseSettings):
    """Environment-driven trust settings independent of routing policy settings."""

    model_config = SettingsConfigDict(
        env_prefix="RUNTIME_AUTHORIZATION_",
        frozen=True,
        extra="ignore",
    )

    required: bool = False
    issuer: str = "verifiable-ai-governance:production"
    audience: str = "policy-model-router"
    trusted_key_set_path: Path | None = None
    agent_bindings_json: str = "{}"
    expected_policy_id: str = "baseline-governance-policy"
    expected_policy_version: str = "1.0.0"
    expected_policy_digest: str = ""
    expected_control_catalog_id: str = "verifiable-ai-governance-baseline"
    expected_control_catalog_version: str = "1.0.0"
    expected_control_catalog_digest: str = ""
    clock_skew_seconds: int = Field(default=0, ge=0, le=60)
    max_key_set_bytes: int = Field(default=262_144, ge=1024, le=2_097_152)
    replay_key_prefix: str = "policy-model-router:runtime-auth:"
    replay_max_entries: int = Field(default=10_000, ge=1, le=1_000_000)

    @field_validator(
        "issuer",
        "audience",
        "expected_policy_id",
        "expected_policy_version",
        "expected_control_catalog_id",
        "expected_control_catalog_version",
        "replay_key_prefix",
    )
    @classmethod
    def _require_non_empty(cls, value: str) -> str:
        """Reject empty trust-boundary identifiers."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("runtime authorization trust identifiers must not be empty")
        return stripped

    @field_validator("trusted_key_set_path", mode="before")
    @classmethod
    def _blank_path_means_unset(cls, value: object) -> object:
        """Treat blank environment values as missing."""
        return None if value == "" else value
