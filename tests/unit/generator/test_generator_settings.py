"""Generator settings: env names, secrets and validation before rent."""

from __future__ import annotations

from pathlib import Path

import pytest

from content_factory.config.settings import GeneratorSettings, load_generator_settings
from content_factory.exceptions import ConfigError


def test_defaults_and_secret_env_names(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("VAST_API_TOKEN", "vast-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "shared-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://llm.test/v1")
    monkeypatch.setenv("GENERATOR_MODEL", "generator-model")
    monkeypatch.setenv("GENERATOR_OUTPUT_DIR", str(tmp_path / "generated"))

    settings = load_generator_settings()

    assert settings.vast_api_token is not None
    assert settings.vast_api_token.get_secret_value() == "vast-secret"
    assert settings.api_key is not None
    assert settings.api_key.get_secret_value() == "shared-secret"
    assert settings.api_url == "http://llm.test/v1"
    assert settings.model == "generator-model"
    assert settings.output_dir == tmp_path / "generated"
    assert settings.max_cost_per_hour is None


def test_generator_defaults_to_the_shared_endpoint(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("GENERATOR_MODEL", raising=False)

    settings = GeneratorSettings()

    assert settings.api_url == "https://ollama.com/v1"
    assert settings.model == "deepseek-v4.1-flash"


def test_blank_optional_values_become_none(monkeypatch) -> None:
    monkeypatch.setenv("GENERATOR_MAX_COST_PER_HOUR", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")

    settings = GeneratorSettings()

    assert settings.max_cost_per_hour is None
    assert settings.api_key is None


def test_rent_pool_size_defaults_to_three() -> None:
    assert GeneratorSettings().rent_pool_size == 3


def test_rent_pool_size_reads_the_env(monkeypatch) -> None:
    monkeypatch.setenv("GENERATOR_RENT_POOL_SIZE", "1")

    assert GeneratorSettings().rent_pool_size == 1


@pytest.mark.parametrize("value", ["0", "9"])
def test_rent_pool_size_rejects_values_outside_the_pool(monkeypatch, value: str) -> None:
    monkeypatch.setenv("GENERATOR_RENT_POOL_SIZE", value)

    with pytest.raises(ConfigError, match="GENERATOR_RENT_POOL_SIZE"):
        load_generator_settings()


def test_offer_refresh_defaults_to_sixty() -> None:
    assert GeneratorSettings().offer_refresh_seconds == 60


def test_offer_refresh_reads_the_env(monkeypatch) -> None:
    monkeypatch.setenv("GENERATOR_OFFER_REFRESH_SECONDS", "30")

    assert GeneratorSettings().offer_refresh_seconds == 30


@pytest.mark.parametrize("value", ["10", "600"])
def test_offer_refresh_accepts_the_window_edges(monkeypatch, value: str) -> None:
    monkeypatch.setenv("GENERATOR_OFFER_REFRESH_SECONDS", value)

    assert GeneratorSettings().offer_refresh_seconds == int(value)


@pytest.mark.parametrize("value", ["9", "601"])
def test_offer_refresh_rejects_values_outside_the_window(monkeypatch, value: str) -> None:
    monkeypatch.setenv("GENERATOR_OFFER_REFRESH_SECONDS", value)

    with pytest.raises(ConfigError, match="GENERATOR_OFFER_REFRESH_SECONDS"):
        load_generator_settings()


def test_bad_openai_base_url_names_the_variable_without_raw_value(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "ftp://secret-host")

    with pytest.raises(ConfigError) as raised:
        load_generator_settings()

    message = str(raised.value)
    assert "настройки генератора: OPENAI_BASE_URL:" in message
    assert "secret-host" not in message


def test_blank_generator_model_names_the_variable(monkeypatch) -> None:
    monkeypatch.setenv("GENERATOR_MODEL", "")

    with pytest.raises(ConfigError, match="настройки генератора: GENERATOR_MODEL:"):
        load_generator_settings()
