"""Environment-only settings for the service."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from content_factory.constants import CONFIG_DIR, CONTENT_DIR, DB_DIR, QUERIES_DIR
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
    max_videos_per_author: int = Field(default=1, ge=1)
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
    if not proxy:
        return
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
    queries_dir: Path,
    content_dir: Path,
    language: str,
) -> SupplierConfig:
    """Merge env settings, runtime limits and paths for one language."""
    return SupplierConfig(
        mcp_url=supplier.mcp_url.strip().rstrip("/"),
        profile_id=supplier.profile_id.strip(),
        bitbrowser_url=supplier.bitbrowser_url.strip().rstrip("/"),
        max_views=supplier.max_views,
        max_followers=supplier.max_followers,
        min_duration=supplier.min_duration,
        max_duration=supplier.max_video_duration,
        min_age_days=supplier.min_age_days,
        max_videos_per_author=supplier.max_videos_per_author,
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
        filter_max=runtime.filter.max,
        filter_pause=runtime.filter.pause,
        download_workers=runtime.download.workers,
        download_pause=runtime.download.pause,
        db_path=db_path,
        queries_dir=queries_dir,
        content_dir=content_dir,
        proxy=supplier.proxy.strip(),
        headless=supplier.headless,
        mcp_timeout=supplier.mcp_timeout,
        close_profile_after_crawl=supplier.close_profile_after_crawl,
        language=language,
    )
