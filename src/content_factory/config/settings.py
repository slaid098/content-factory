"""Environment-only settings for the service."""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from content_factory.constants import (
    ARCHIVE_DIR,
    CONFIG_DIR,
    CONTENT_DIR,
    DB_DIR,
    GENERATED_DIR,
    QUERIES_DIR,
)
from content_factory.exceptions.errors import ConfigError
from content_factory.schemas.config.runtime import RuntimeConfig
from content_factory.schemas.supplier.settings import SupplierConfig

_PROXY_SCHEMES = frozenset({"http", "https", "socks5", "socks5h"})
_RANGE_PAIRS = (
    ("scrolls_min", "scrolls_max", "SUPPLIER_SCROLLS"),
    (
        "queries_per_session_min",
        "queries_per_session_max",
        "SUPPLIER_QUERIES_PER_SESSION",
    ),
    ("pause_scroll_min", "pause_scroll_max", "SUPPLIER_PAUSE_SCROLL"),
    ("page_settle_min", "page_settle_max", "SUPPLIER_PAGE_SETTLE"),
    ("session_break_min", "session_break_max", "SUPPLIER_SESSION_BREAK"),
    ("captcha_cooldown_min", "captcha_cooldown_max", "SUPPLIER_CAPTCHA_COOLDOWN"),
)


def _blank_to_none_value(value: object) -> object:
    """Treat a blank string as an unset optional value."""
    if isinstance(value, str) and not value.strip():
        return None
    return value


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
    delivery_token: str | None = None
    delivery_retention_hours: int = Field(default=24, ge=1)

    @field_validator("data_dir")
    @classmethod
    def _expand_user(cls, value: Path) -> Path:
        return value.expanduser()

    @field_validator(
        "telegram_bot_token",
        "telegram_chat_id",
        "delivery_token",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        return _blank_to_none_value(value)


class SupplierSettings(BaseSettings):
    """TikTok supplier settings read from ``SUPPLIER_*`` variables.

    Pacing limits that must change on disk without a restart (queries, filter
    batch and download workers) live in ``RuntimeConfig`` instead.
    """

    model_config = SettingsConfigDict(
        env_prefix="SUPPLIER_",
        extra="ignore",
        case_sensitive=False,
    )

    mcp_url: str = "http://127.0.0.1:8765"
    profile_id: str = ""
    bitbrowser_url: str = "http://127.0.0.1:54529"
    max_views: int = Field(default=1000, ge=0)
    max_followers: int = Field(default=500, ge=0)
    min_duration: int = Field(default=5, ge=0)
    max_video_duration: int = Field(default=60, ge=0)
    min_age_days: int = Field(default=5, ge=0)
    # The engine enforces one video per author; the donor env key stays accepted
    # only with the single valid value.
    max_videos_per_author: int = Field(default=1, ge=1, le=1)
    scrolls_min: int = Field(default=5, ge=0)
    scrolls_max: int = Field(default=8, ge=0)
    queries_per_session_min: int = Field(default=12, ge=0)
    queries_per_session_max: int = Field(default=18, ge=0)
    pause_scroll_min: float = Field(default=1.5, ge=0)
    pause_scroll_max: float = Field(default=4.0, ge=0)
    page_settle_min: float = Field(default=3.0, ge=0)
    page_settle_max: float = Field(default=6.0, ge=0)
    session_break_min: int = Field(default=180, ge=0)
    session_break_max: int = Field(default=300, ge=0)
    captcha_cooldown_min: int = Field(default=900, ge=0)
    captcha_cooldown_max: int = Field(default=1800, ge=0)
    probe_workers: int = Field(default=8, ge=1)
    proxy: str = ""
    headless: bool = False
    mcp_timeout: int = Field(default=90, ge=1)
    close_profile_after_crawl: bool = True

    @model_validator(mode="after")
    def _check_ranges(self) -> SupplierSettings:
        if self.min_duration > self.max_video_duration:
            raise ValueError(
                "SUPPLIER_MIN_DURATION не может быть больше SUPPLIER_MAX_VIDEO_DURATION"
            )
        for minimum, maximum, label in _RANGE_PAIRS:
            if getattr(self, minimum) > getattr(self, maximum):
                raise ValueError(f"{label}: минимум не может быть больше максимума")
        return self


class _OpenAIEndpointSettings(BaseSettings):
    """Shared OpenAI-compatible endpoint read from ``OPENAI_*`` variables.

    Both the title flow and the generator talk to the same API, so the key and
    the address are project-wide. A task only picks its own model.
    """

    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    api_key: SecretStr | None = Field(default=None, validation_alias="OPENAI_API_KEY")
    api_url: str = Field(
        default="https://ollama.com/v1",
        validation_alias="OPENAI_BASE_URL",
    )

    @field_validator("api_url")
    @classmethod
    def _check_api_url(cls, value: str) -> str:
        if any(char.isspace() for char in value):
            raise ValueError("адрес не должен содержать пробелы")
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"}:
            raise ValueError("адрес должен начинаться с http:// или https://")
        if not parts.hostname:
            raise ValueError("в адресе не указан хост")
        try:
            _ = parts.port
        except ValueError as ex:
            raise ValueError("в адресе неверный порт") from ex
        if value.endswith("/"):
            raise ValueError("адрес не должен заканчиваться на /")
        return value

    @field_validator("api_key", mode="before")
    @classmethod
    def _blank_key_to_none(cls, value: object) -> object:
        return _blank_to_none_value(value)


class TitlesSettings(_OpenAIEndpointSettings):
    """Settings of the title generation flow read from ``TITLES_*`` variables."""

    model_config = SettingsConfigDict(
        env_prefix="TITLES_",
        extra="ignore",
        case_sensitive=False,
    )

    model: str = Field(default="deepseek-v4.1-flash", min_length=1)
    timeout_seconds: float = Field(default=60, ge=5, le=300)
    reasoning_effort: Literal["none", "low", "high", "max"] = "none"


class GeneratorSettings(_OpenAIEndpointSettings):
    """Settings of the dating batch generator read from ``GENERATOR_*`` variables.

    ``vast_api_token`` is read from ``VAST_API_TOKEN``: the CLI of the
    ``vastai`` package uses that exact name in the deployment. Secrets stay
    :class:`SecretStr` and never reach logs, ``run.json`` or error messages.
    """

    model_config = SettingsConfigDict(
        env_prefix="GENERATOR_",
        extra="ignore",
        case_sensitive=False,
    )

    vast_api_token: SecretStr | None = Field(default=None, validation_alias="VAST_API_TOKEN")
    model: str = Field(default="deepseek-v4.1-flash", min_length=1)
    server_ready_timeout: int = Field(default=3600, ge=60)
    rent_pool_size: int = Field(default=3, ge=1, le=8)
    comfy_health_timeout: int = Field(default=300, ge=10)
    max_cost_per_hour: float | None = Field(default=None, gt=0)
    stale_instance_hours: int = Field(default=6, ge=1)
    output_dir: Path = Path("./app_data/generated")

    @field_validator("output_dir")
    @classmethod
    def _expand_output_dir(cls, value: Path) -> Path:
        return value.expanduser()

    @field_validator("vast_api_token", "max_cost_per_hour", mode="before")
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        return _blank_to_none_value(value)


def _ensure_directories(data_dir: Path) -> None:
    for name in (CONFIG_DIR, DB_DIR, CONTENT_DIR, ARCHIVE_DIR, QUERIES_DIR, GENERATED_DIR):
        path = data_dir / name
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as ex:
            raise ConfigError(f"не удалось создать каталог {path}: {ex}") from ex


@cache
def get_settings() -> Settings:
    """Return the process settings, reading the environment only once."""
    return Settings()


def load_settings() -> Settings:
    """Read the process settings from the environment once.

    Ensure the data directory tree exists; later calls reuse the cached
    settings without reading the environment again.
    """
    settings = get_settings()
    prepare_data_dirs(settings)
    return settings


def prepare_data_dirs(settings: Settings) -> None:
    """Create the runtime directory tree for callers that already hold settings."""
    _ensure_directories(settings.data_dir)


def load_supplier_settings() -> SupplierSettings:
    """Read the TikTok supplier settings from the environment.

    Raises:
        ConfigError: A value cannot be parsed or violates a limit. The
            message never repeats raw input, so proxy credentials do not leak.
    """
    try:
        settings = SupplierSettings()
    except ValidationError as ex:
        raise ConfigError(
            f"не удалось загрузить настройки поставщика: {_validation_message(ex)}"
        ) from ex
    _validate_proxy(settings.proxy)
    return settings


def load_titles_settings() -> TitlesSettings:
    """Read the title generation settings from the environment.

    Raises:
        ConfigError: A value cannot be parsed or violates a limit. The message
            never repeats raw input, so the API key does not leak.
    """
    try:
        return TitlesSettings()
    except ValidationError as ex:
        raise ConfigError(
            f"не удалось загрузить настройки названий: {_titles_validation_message(ex)}"
        ) from ex


def _titles_validation_message(error: ValidationError) -> str:
    """Format title settings errors without the raw input values."""
    return _settings_validation_message(
        error,
        settings_cls=TitlesSettings,
        default_label="настройки названий",
    )


def load_generator_settings() -> GeneratorSettings:
    """Read the generator settings from the environment.

    Raises:
        ConfigError: A value cannot be parsed or violates a limit. The message
            never repeats raw input, so the Vast.ai token and the OpenAI key
            do not leak.
    """
    try:
        return GeneratorSettings()
    except ValidationError as ex:
        message = _generator_message(ex)
        raise ConfigError(f"не удалось загрузить настройки генератора: {message}") from ex


def _generator_message(error: ValidationError) -> str:
    """Format generator settings errors without the raw input values."""
    return _settings_validation_message(
        error,
        settings_cls=GeneratorSettings,
        default_label="настройки генератора",
    )


def _settings_validation_message(
    error: ValidationError,
    *,
    settings_cls: type[BaseSettings],
    default_label: str,
) -> str:
    """Name the environment variable of every invalid field, never its value."""
    names = _env_names(settings_cls)
    parts: list[str] = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"])
        label = names.get(location, location if location else default_label)
        parts.append(f"{label}: {item['msg']}")
    return "; ".join(parts)


def _env_names(settings_cls: type[BaseSettings]) -> dict[str, str]:
    """Map every field and its alias to the environment variable it reads."""
    prefix = settings_cls.model_config["env_prefix"]
    names: dict[str, str] = {}
    for name, field in settings_cls.model_fields.items():
        alias = field.validation_alias
        env_name = alias if isinstance(alias, str) else f"{prefix}{name.upper()}"
        names[name] = env_name
        if isinstance(alias, str):
            names[alias] = env_name
    return names


def _validation_message(error: ValidationError) -> str:
    """Format validation errors without the raw input values."""
    parts: list[str] = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"])
        label = f"SUPPLIER_{location.upper()}" if location else "настройки поставщика"
        parts.append(f"{label}: {item['msg']}")
    return "; ".join(parts)


def _validate_proxy(proxy: str) -> None:
    """Reject proxies with an unknown scheme or an unparsable host."""
    proxy = proxy.strip()
    if not proxy:
        return
    if any(char.isspace() for char in proxy):
        raise ConfigError("SUPPLIER_PROXY не должен содержать пробелы")
    try:
        parts = urlsplit(proxy)
        _ = parts.port
    except ValueError as ex:
        raise ConfigError("некорректный SUPPLIER_PROXY: не удалось разобрать порт") from ex
    if parts.scheme.lower() not in _PROXY_SCHEMES:
        raise ConfigError(
            "SUPPLIER_PROXY должен начинаться с http://, https://, socks5:// или socks5h://"
        )
    if not parts.hostname:
        raise ConfigError("в SUPPLIER_PROXY не указан хост")


def build_supplier_config(
    supplier: SupplierSettings,
    runtime: RuntimeConfig,
    *,
    db_path: Path,
    content_dir: Path,
    language: str,
) -> SupplierConfig:
    """Merge env settings, runtime limits and paths for one language."""
    return SupplierConfig(
        profile_id=supplier.profile_id.strip(),
        bitbrowser_url=supplier.bitbrowser_url.strip().rstrip("/"),
        max_views=supplier.max_views,
        max_followers=supplier.max_followers,
        min_duration=supplier.min_duration,
        max_duration=supplier.max_video_duration,
        min_age_days=supplier.min_age_days,
        max_queries=runtime.search.max_queries,
        scrolls_min=supplier.scrolls_min,
        scrolls_max=supplier.scrolls_max,
        queries_per_session_min=supplier.queries_per_session_min,
        queries_per_session_max=supplier.queries_per_session_max,
        search_pause=runtime.search.pause,
        pause_scroll_min=supplier.pause_scroll_min,
        pause_scroll_max=supplier.pause_scroll_max,
        page_settle_min=supplier.page_settle_min,
        page_settle_max=supplier.page_settle_max,
        session_break_min=supplier.session_break_min,
        session_break_max=supplier.session_break_max,
        captcha_cooldown_min=supplier.captcha_cooldown_min,
        captcha_cooldown_max=supplier.captcha_cooldown_max,
        probe_workers=supplier.probe_workers,
        filter_pause=runtime.filter.pause,
        download_workers=runtime.download.workers,
        download_pause=runtime.download.pause,
        db_path=db_path,
        content_dir=content_dir,
        proxy=supplier.proxy.strip(),
        language=language,
    )
