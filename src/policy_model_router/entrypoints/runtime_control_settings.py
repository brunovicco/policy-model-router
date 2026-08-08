"""Runtime-control settings for Governance emergency-stop enforcement."""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RuntimeControlSettings(BaseSettings):
    """Environment-driven settings for the shared Governance runtime projection."""

    model_config = SettingsConfigDict(
        env_prefix="RUNTIME_CONTROL_",
        frozen=True,
        extra="ignore",
    )

    required: bool = False
    redis_key_prefix: str = "verifiable-ai-governance:runtime-control:v1:agent:"
    max_snapshot_bytes: int = Field(default=4096, ge=512, le=65_536)
    timeout_seconds: float = Field(default=2.0, gt=0, le=10)

    @field_validator("redis_key_prefix")
    @classmethod
    def _require_key_prefix(cls, value: str) -> str:
        """Require an explicit non-empty namespace shared with Governance P1.6a."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("runtime-control Redis key prefix must not be empty")
        return stripped
