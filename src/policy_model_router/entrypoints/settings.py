"""Typed, validated runtime configuration read from environment variables.

Centralizes env var parsing that used to be scattered as ad-hoc ``os.environ.get(...)`` calls plus
manual ``int()``/``float()`` casts across ``entrypoints/http.py`` and ``entrypoints/logging.py``. A
malformed value (e.g. ``RATE_LIMIT_MAX_REQUESTS=not-a-number``) now fails closed with a clear
validation error at startup, instead of an opaque ``ValueError`` deep inside a cast call.

``API_KEYS`` is deliberately not modeled here: its JSON-object shape and per-key non-empty-string
validation is more specific than a scalar env var, and its existing hand-written validator
(``entrypoints/http.py::_required_api_keys``) is already well-tested; duplicating that logic into a
generic settings field would risk changing its carefully-worded fail-closed error messages for no
real benefit.
"""

import math
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_AppEnv = Literal["development", "staging", "production", "test"]
_LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
_LogFormat = Literal["json", "console"]


class Settings(BaseSettings):
    """Runtime configuration, read once from the environment and immutable afterward.

    Field names map to environment variables case-insensitively (e.g. ``routing_policy_path`` <->
    ``ROUTING_POLICY_PATH``) - this is ``pydantic-settings``' default behavior, not a custom alias
    per field.
    """

    model_config = SettingsConfigDict(frozen=True, extra="ignore")

    routing_policy_path: Path = Field(default=Path("config/routing_policy.yaml"))
    app_env: _AppEnv = Field(default="development")
    log_level: _LogLevel = Field(default="INFO")
    log_format: _LogFormat = Field(default="json")
    rate_limit_max_requests: int = Field(default=60, gt=0)
    rate_limit_per_ip_max_requests: int = Field(default=600, gt=0)
    rate_limit_window_seconds: float = Field(default=60.0, gt=0)
    rate_limit_max_tracked_keys: int = Field(default=100_000, gt=0)
    rate_limit_fingerprint_secret: str | None = Field(default=None)
    redis_url: str | None = Field(default=None)
    enable_api_docs: bool = Field(default=False)

    @field_validator("redis_url", "rate_limit_fingerprint_secret", mode="before")
    @classmethod
    def _blank_env_value_means_unset(cls, value: str | None) -> str | None:
        """Treat an explicitly-set-but-empty env var the same as an unset one.

        ``REDIS_URL=`` in a ``.env`` file or a container's environment block is a common way to
        leave a variable present-but-empty; without this, it would be treated as
        ``redis_url=""`` (truthy as a string, wrong once used as a URL) rather than "not
        configured", diverging from the pre-existing ``if not redis_url:`` check this replaces.
        """
        return value or None

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        """Uppercase/strip before the closed-vocabulary check, keeping prior case-insensitivity."""
        return value.strip().upper() if isinstance(value, str) else value

    @field_validator("log_format", mode="before")
    @classmethod
    def _normalize_log_format(cls, value: object) -> object:
        """Lowercase/strip before the closed-vocabulary check, keeping prior case-insensitivity."""
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("rate_limit_window_seconds")
    @classmethod
    def _must_be_finite(cls, value: float) -> float:
        """Reject non-finite values: ``gt=0`` alone does not exclude ``inf``, which breaks Redis."""
        if not math.isfinite(value):
            raise ValueError("rate_limit_window_seconds must be a finite number")
        return value
