"""Device-gate settings read from ``CONTENT_FACTORY_*`` variables."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from content_factory.config.settings import Settings

_DEVICE_ENV = (
    "CONTENT_FACTORY_DEVICE_GATE_ENABLED",
    "CONTENT_FACTORY_DEVICE_LINK_TTL_HOURS",
    "CONTENT_FACTORY_DEVICE_TTL_DAYS",
    "CONTENT_FACTORY_DEVICE_COOKIE_SECURE",
    "CONTENT_FACTORY_PUBLIC_BASE_URL",
)


def _clear_device_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _DEVICE_ENV:
        monkeypatch.delenv(name, raising=False)


def test_gate_is_enabled_by_default_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CONTENT_FACTORY_DEVICE_GATE_ENABLED", raising=False)

    assert Settings().device_gate_enabled is True


def test_device_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_device_env(monkeypatch)

    settings = Settings()

    assert settings.device_gate_enabled is True
    assert settings.device_link_ttl_hours == 24
    assert settings.device_ttl_days == 180
    assert settings.device_cookie_secure is True
    assert settings.public_base_url == "https://content.slaid098.dev"


def test_device_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_device_env(monkeypatch)
    monkeypatch.setenv("CONTENT_FACTORY_DEVICE_GATE_ENABLED", "false")
    monkeypatch.setenv("CONTENT_FACTORY_DEVICE_LINK_TTL_HOURS", "6")
    monkeypatch.setenv("CONTENT_FACTORY_DEVICE_TTL_DAYS", "30")
    monkeypatch.setenv("CONTENT_FACTORY_DEVICE_COOKIE_SECURE", "false")
    monkeypatch.setenv("CONTENT_FACTORY_PUBLIC_BASE_URL", "https://example.test")

    settings = Settings()

    assert settings.device_gate_enabled is False
    assert settings.device_link_ttl_hours == 6
    assert settings.device_ttl_days == 30
    assert settings.device_cookie_secure is False
    assert settings.public_base_url == "https://example.test"


@pytest.mark.parametrize("value", ["0", "721"])
def test_link_ttl_bounds_are_validated(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    _clear_device_env(monkeypatch)
    monkeypatch.setenv("CONTENT_FACTORY_DEVICE_LINK_TTL_HOURS", value)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("value", ["0", "3651"])
def test_device_ttl_bounds_are_validated(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    _clear_device_env(monkeypatch)
    monkeypatch.setenv("CONTENT_FACTORY_DEVICE_TTL_DAYS", value)

    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("value", ["", "   ", "https://content.slaid098.dev/"])
def test_public_base_url_rejects_empty_and_trailing_slash(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    _clear_device_env(monkeypatch)
    monkeypatch.setenv("CONTENT_FACTORY_PUBLIC_BASE_URL", value)

    with pytest.raises(ValidationError):
        Settings()
