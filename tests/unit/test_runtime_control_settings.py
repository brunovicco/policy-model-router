"""Configuration tests for P1.6b runtime-control enforcement."""

import pytest
from pydantic import ValidationError

from policy_model_router.entrypoints.runtime_control_settings import RuntimeControlSettings


def test_runtime_control_defaults_match_governance_projection_contract() -> None:
    settings = RuntimeControlSettings(_env_file=None)

    assert settings.required is False
    assert settings.redis_key_prefix == "verifiable-ai-governance:runtime-control:v1:agent:"
    assert settings.max_snapshot_bytes == 4096
    assert settings.timeout_seconds == 2.0


def test_runtime_control_rejects_empty_key_prefix() -> None:
    with pytest.raises(ValidationError):
        RuntimeControlSettings(redis_key_prefix="   ", _env_file=None)
