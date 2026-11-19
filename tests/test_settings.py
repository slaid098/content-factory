from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from content_factory.config.settings import Settings, load_settings
from content_factory.constants import CONFIG_DIR, CONTENT_DIR, DB_DIR, QUERIES_DIR
from content_factory.exceptions import ConfigError

_ENV_NAMES = (
    "CONTENT_FACTORY_DATA_DIR",
    "CONTENT_FACTORY_LOG_LEVEL",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
)


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)

    settings = Settings()

    assert settings.data_dir == Path("app_data")
    assert settings.log_level == "INFO"
    assert settings.telegram_bot_token is None
    assert settings.telegram_chat_id is None


def test_environment_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTENT_FACTORY_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")

    settings = Settings()

    assert settings.data_dir == tmp_path
    assert settings.log_level == "DEBUG"
    assert settings.telegram_bot_token == "token"
    assert settings.telegram_chat_id == "42"


def test_blank_telegram_values_become_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "   ")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "")

    settings = Settings()

    assert settings.telegram_bot_token is None
    assert settings.telegram_chat_id is None


def test_unknown_log_level_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("CONTENT_FACTORY_LOG_LEVEL", "TRACE")

    with pytest.raises(ValidationError):
        Settings()


def test_load_settings_creates_data_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_env(monkeypatch)
    data_dir = tmp_path / "data"
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(data_dir))

    settings = load_settings()

    assert settings.data_dir == data_dir
    for name in (CONFIG_DIR, DB_DIR, CONTENT_DIR, QUERIES_DIR):
        assert (data_dir / name).is_dir()


def test_load_settings_wraps_filesystem_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path / "data"))

    def _deny(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "mkdir", _deny)

    with pytest.raises(ConfigError, match="permission denied"):
        load_settings()
