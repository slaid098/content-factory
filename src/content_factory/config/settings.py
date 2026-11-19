"""Environment-only settings for the service."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from content_factory.constants import CONFIG_DIR, CONTENT_DIR, DB_DIR, QUERIES_DIR
from content_factory.exceptions.errors import ConfigError


class Settings(BaseSettings):
    """Runtime settings read from the process environment."""

    model_config = SettingsConfigDict(
        env_prefix="CONTENT_FACTORY_",
        extra="ignore",
    )

    data_dir: Path = Path("./app_data")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    telegram_bot_token: str | None = Field(default=None, validation_alias="TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str | None = Field(default=None, validation_alias="TELEGRAM_CHAT_ID")

    @field_validator("data_dir")
    @classmethod
    def _expand_user(cls, value: Path) -> Path:
        return value.expanduser()

    @field_validator("telegram_bot_token", "telegram_chat_id", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value


def _ensure_directories(data_dir: Path) -> None:
    for name in (CONFIG_DIR, DB_DIR, CONTENT_DIR, QUERIES_DIR):
        path = data_dir / name
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as ex:
            raise ConfigError(f"не удалось создать каталог {path}: {ex}") from ex


def load_settings() -> Settings:
    """Read settings from the environment and prepare the data directory."""
    settings = Settings()
    _ensure_directories(settings.data_dir)
    return settings
