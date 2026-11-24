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


def test_stale_instance_hours_defaults_to_three() -> None:
    assert GeneratorSettings().stale_instance_hours == 3


def test_stale_instance_hours_reads_the_env(monkeypatch) -> None:
    monkeypatch.setenv("GENERATOR_STALE_INSTANCE_HOURS", "5")

    assert GeneratorSettings().stale_instance_hours == 5


def test_stale_instance_hours_rejects_zero(monkeypatch) -> None:
    monkeypatch.setenv("GENERATOR_STALE_INSTANCE_HOURS", "0")

    with pytest.raises(ConfigError, match="GENERATOR_STALE_INSTANCE_HOURS"):
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


def test_safety_defaults_are_on_with_twelve_frames() -> None:
    settings = GeneratorSettings()

    assert settings.safety_enabled is True
    assert settings.safety_frames == 12
    assert settings.safety_threshold == 0.5
    assert settings.safety_nude_threshold == 0.3
    assert settings.safety_frame_attempts == 2
    assert settings.clef_model == "clef"


def test_safety_reads_the_env(monkeypatch) -> None:
    monkeypatch.setenv("GENERATOR_SAFETY_ENABLED", "false")
    monkeypatch.setenv("GENERATOR_SAFETY_FRAMES", "24")
    monkeypatch.setenv("GENERATOR_SAFETY_THRESHOLD", "0.7")
    monkeypatch.setenv("GENERATOR_SAFETY_NUDE_THRESHOLD", "0.2")
    monkeypatch.setenv("GENERATOR_SAFETY_FRAME_ATTEMPTS", "3")
    monkeypatch.setenv("GENERATOR_CLEF_MODEL", "clef-flash")

    settings = GeneratorSettings()

    assert settings.safety_enabled is False
    assert settings.safety_frames == 24
    assert settings.safety_threshold == 0.7
    assert settings.safety_nude_threshold == 0.2
    assert settings.safety_frame_attempts == 3
    assert settings.clef_model == "clef-flash"


@pytest.mark.parametrize("value", ["3", "24"])
def test_safety_frames_accepts_the_window_edges(monkeypatch, value: str) -> None:
    monkeypatch.setenv("GENERATOR_SAFETY_FRAMES", value)

    assert GeneratorSettings().safety_frames == int(value)


@pytest.mark.parametrize("value", ["2", "25"])
def test_safety_frames_rejects_values_outside_the_window(monkeypatch, value: str) -> None:
    monkeypatch.setenv("GENERATOR_SAFETY_FRAMES", value)

    with pytest.raises(ConfigError, match="GENERATOR_SAFETY_FRAMES"):
        load_generator_settings()


@pytest.mark.parametrize("value", ["1", "3"])
def test_safety_frame_attempts_accepts_the_window_edges(monkeypatch, value: str) -> None:
    monkeypatch.setenv("GENERATOR_SAFETY_FRAME_ATTEMPTS", value)

    assert GeneratorSettings().safety_frame_attempts == int(value)


@pytest.mark.parametrize("value", ["0", "4"])
def test_safety_frame_attempts_rejects_values_outside_the_window(monkeypatch, value: str) -> None:
    monkeypatch.setenv("GENERATOR_SAFETY_FRAME_ATTEMPTS", value)

    with pytest.raises(ConfigError, match="GENERATOR_SAFETY_FRAME_ATTEMPTS"):
        load_generator_settings()


@pytest.mark.parametrize(
    "name",
    ["GENERATOR_SAFETY_THRESHOLD", "GENERATOR_SAFETY_NUDE_THRESHOLD"],
)
@pytest.mark.parametrize("value", ["0", "1.1"])
def test_safety_thresholds_stay_inside_probability(monkeypatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ConfigError, match=name):
        load_generator_settings()


def test_comfy_job_timeout_and_health_interval_defaults() -> None:
    settings = GeneratorSettings()

    assert settings.comfy_job_timeout == 1800
    assert settings.comfy_health_interval == 60


def test_comfy_job_timeout_and_health_interval_read_the_env(monkeypatch) -> None:
    monkeypatch.setenv("GENERATOR_COMFY_JOB_TIMEOUT", "900")
    monkeypatch.setenv("GENERATOR_COMFY_HEALTH_INTERVAL", "30")

    settings = GeneratorSettings()

    assert settings.comfy_job_timeout == 900
    assert settings.comfy_health_interval == 30


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("GENERATOR_COMFY_JOB_TIMEOUT", "299"),
        ("GENERATOR_COMFY_HEALTH_INTERVAL", "9"),
    ],
)
def test_comfy_limits_reject_values_below_the_floor(monkeypatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ConfigError, match=name):
        load_generator_settings()
