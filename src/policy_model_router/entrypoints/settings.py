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

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, read once from the environment and immutable afterward.

    Field names map to environment variables case-insensitively (e.g. ``routing_policy_path`` <->
    ``ROUTING_POLICY_PATH``) - this is ``pydantic-settings``' default behavior, not a custom alias
    per field.
    """

    model_config = SettingsConfigDict(frozen=True, extra="ignore")

    routing_policy_path: Path = Field(default=Path("config/routing_policy.yaml"))
    app_env: str = Field(default="development")
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="json")
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
