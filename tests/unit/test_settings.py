"""Unit tests for the typed runtime :class:`~policy_model_router.entrypoints.settings.Settings`."""

from pathlib import Path

import pydantic
import pytest

from policy_model_router.entrypoints.settings import Settings


def test_settings_uses_documented_defaults_when_nothing_is_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in (
        "ROUTING_POLICY_PATH",
        "APP_ENV",
        "LOG_LEVEL",
        "LOG_FORMAT",
        "RATE_LIMIT_MAX_REQUESTS",
        "RATE_LIMIT_PER_IP_MAX_REQUESTS",
        "RATE_LIMIT_WINDOW_SECONDS",
        "RATE_LIMIT_MAX_TRACKED_KEYS",
        "RATE_LIMIT_FINGERPRINT_SECRET",
        "REDIS_URL",
        "ENABLE_API_DOCS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings()

    assert settings.routing_policy_path == Path("config/routing_policy.yaml")
    assert settings.app_env == "development"
    assert settings.log_level == "INFO"
    assert settings.log_format == "json"
    assert settings.rate_limit_max_requests == 60
    assert settings.rate_limit_per_ip_max_requests == 600
    assert settings.rate_limit_window_seconds == 60.0
    assert settings.rate_limit_max_tracked_keys == 100_000
    assert settings.rate_limit_fingerprint_secret is None
    assert settings.redis_url is None
    assert settings.enable_api_docs is False


def test_settings_reads_env_vars_case_insensitively(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("routing_policy_path", "/etc/custom_policy.yaml")
    monkeypatch.setenv("Rate_Limit_Max_Requests", "10")

    settings = Settings()

    assert settings.routing_policy_path == Path("/etc/custom_policy.yaml")
    assert settings.rate_limit_max_requests == 10


def test_settings_parses_typed_fields_from_their_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RATE_LIMIT_MAX_REQUESTS", "10")
    monkeypatch.setenv("RATE_LIMIT_PER_IP_MAX_REQUESTS", "100")
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "30.5")
    monkeypatch.setenv("RATE_LIMIT_MAX_TRACKED_KEYS", "500")
    monkeypatch.setenv("ENABLE_API_DOCS", "true")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("RATE_LIMIT_FINGERPRINT_SECRET", "shared-secret")

    settings = Settings()

    assert settings.rate_limit_max_requests == 10
    assert settings.rate_limit_per_ip_max_requests == 100
    assert settings.rate_limit_window_seconds == 30.5
    assert settings.rate_limit_max_tracked_keys == 500
    assert settings.enable_api_docs is True
    assert settings.redis_url == "redis://localhost:6379/0"
    assert settings.rate_limit_fingerprint_secret == "shared-secret"  # noqa: S105 - test fixture value, not a real secret


def test_settings_fails_closed_on_a_non_numeric_rate_limit_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RATE_LIMIT_MAX_REQUESTS", "not-a-number")

    with pytest.raises(pydantic.ValidationError, match="rate_limit_max_requests"):
        Settings()


def test_settings_fails_closed_on_a_non_positive_rate_limit_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RATE_LIMIT_MAX_REQUESTS", "0")

    with pytest.raises(pydantic.ValidationError):
        Settings()


def test_settings_treats_an_empty_redis_url_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REDIS_URL", "")

    assert Settings().redis_url is None


def test_settings_treats_an_empty_fingerprint_secret_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RATE_LIMIT_FINGERPRINT_SECRET", "")

    assert Settings().rate_limit_fingerprint_secret is None


def test_settings_is_immutable() -> None:
    settings = Settings()

    with pytest.raises(pydantic.ValidationError):
        settings.app_env = "production"  # type: ignore[misc]  # intentional: proving frozen=True raises


def test_settings_fails_closed_on_an_empty_app_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "")

    with pytest.raises(pydantic.ValidationError, match="app_env"):
        Settings()


def test_settings_fails_closed_on_an_unknown_app_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "not-a-real-environment")

    with pytest.raises(pydantic.ValidationError, match="app_env"):
        Settings()


def test_settings_fails_closed_on_an_unknown_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "NOPE")

    with pytest.raises(pydantic.ValidationError, match="log_level"):
        Settings()


def test_settings_accepts_a_mixed_case_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "debug")

    assert Settings().log_level == "DEBUG"


def test_settings_fails_closed_on_an_unknown_log_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_FORMAT", "xml")

    with pytest.raises(pydantic.ValidationError, match="log_format"):
        Settings()


def test_settings_accepts_a_mixed_case_log_format(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_FORMAT", "CONSOLE")

    assert Settings().log_format == "console"


def test_settings_fails_closed_on_an_infinite_rate_limit_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RATE_LIMIT_WINDOW_SECONDS", "inf")

    with pytest.raises(pydantic.ValidationError, match="rate_limit_window_seconds"):
        Settings()
