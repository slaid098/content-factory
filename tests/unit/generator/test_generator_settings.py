"""Generator settings: env names, secrets and validation before rent."""

from __future__ import annotations

from pathlib import Path

import pytest

from content_factory.config.settings import GeneratorSettings, load_generator_settings
from content_factory.exceptions import ConfigError


def test_defaults_and_secret_env_names(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VAST_API_TOKEN", "vast-secret")
    monkeypatch.setenv("GENERATOR_AI_KEY", "ai-secret")
    monkeypatch.setenv("GENERATOR_OUTPUT_DIR", str(tmp_path / "generated"))

    settings = load_generator_settings()

    assert settings.vast_api_token is not None
    assert settings.vast_api_token.get_secret_value() == "vast-secret"
    assert settings.ai_key is not None
    assert settings.ai_key.get_secret_value() == "ai-secret"
    assert settings.output_dir == tmp_path / "generated"
    assert settings.ai_url == "https://ollama.com/v1"
    assert settings.ai_model == "deepseek-v4.1-flash"
    assert settings.max_cost_per_hour is None


def test_blank_optional_values_become_none(monkeypatch) -> None:
    monkeypatch.setenv("GENERATOR_MAX_COST_PER_HOUR", "")
    monkeypatch.setenv("GENERATOR_AI_KEY", "")

    settings = GeneratorSettings()

    assert settings.max_cost_per_hour is None
    assert settings.ai_key is None


def test_bad_ai_url_names_the_variable_without_raw_value(monkeypatch) -> None:
    monkeypatch.setenv("GENERATOR_AI_URL", "ftp://secret-host")

    with pytest.raises(ConfigError) as raised:
        load_generator_settings()

    message = str(raised.value)
    assert "GENERATOR_AI_URL" in message
    assert "secret-host" not in message
