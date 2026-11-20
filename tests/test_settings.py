from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from content_factory.config.settings import (
    Settings,
    SupplierSettings,
    build_supplier_config,
    load_settings,
    load_supplier_settings,
)
from content_factory.constants import (
    ARCHIVE_DIR,
    CONFIG_DIR,
    CONTENT_DIR,
    DB_DIR,
    QUERIES_DIR,
)
from content_factory.exceptions import ConfigError
from content_factory.schemas.config.runtime import (
    AlertsConfig,
    CaptchaConfig,
    DownloadConfig,
    FilterConfig,
    LanguageConfig,
    RunConfig,
    RuntimeConfig,
    SearchConfig,
)

_ENV_NAMES = (
    "CONTENT_FACTORY_DATA_DIR",
    "CONTENT_FACTORY_LOG_LEVEL",
    "CONTENT_FACTORY_DELIVERY_TOKEN",
    "CONTENT_FACTORY_DELIVERY_RETENTION_HOURS",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
)

_SUPPLIER_ENV_NAMES = (
    "SUPPLIER_MCP_URL",
    "SUPPLIER_PROFILE_ID",
    "SUPPLIER_BITBROWSER_URL",
    "SUPPLIER_MAX_VIEWS",
    "SUPPLIER_MAX_FOLLOWERS",
    "SUPPLIER_MIN_DURATION",
    "SUPPLIER_MAX_VIDEO_DURATION",
    "SUPPLIER_MIN_AGE_DAYS",
    "SUPPLIER_MAX_VIDEOS_PER_AUTHOR",
    "SUPPLIER_SCROLLS_MIN",
    "SUPPLIER_SCROLLS_MAX",
    "SUPPLIER_QUERIES_PER_SESSION_MIN",
    "SUPPLIER_QUERIES_PER_SESSION_MAX",
    "SUPPLIER_PAUSE_SCROLL_MIN",
    "SUPPLIER_PAUSE_SCROLL_MAX",
    "SUPPLIER_PAGE_SETTLE_MIN",
    "SUPPLIER_PAGE_SETTLE_MAX",
    "SUPPLIER_SESSION_BREAK_MIN",
    "SUPPLIER_SESSION_BREAK_MAX",
    "SUPPLIER_CAPTCHA_COOLDOWN_MIN",
    "SUPPLIER_CAPTCHA_COOLDOWN_MAX",
    "SUPPLIER_PROBE_WORKERS",
    "SUPPLIER_PROXY",
    "SUPPLIER_HEADLESS",
    "SUPPLIER_MCP_TIMEOUT",
    "SUPPLIER_CLOSE_PROFILE_AFTER_CRAWL",
)


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def _clear_supplier_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _SUPPLIER_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)

    settings = Settings()

    assert settings.data_dir == Path("app_data")
    assert settings.log_level == "INFO"
    assert settings.telegram_bot_token is None
    assert settings.telegram_chat_id is None
    assert settings.delivery_token is None
    assert settings.delivery_retention_hours == 24


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


def test_delivery_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("CONTENT_FACTORY_DELIVERY_TOKEN", "secret")
    monkeypatch.setenv("CONTENT_FACTORY_DELIVERY_RETENTION_HOURS", "6")

    settings = Settings()

    assert settings.delivery_token == "secret"
    assert settings.delivery_retention_hours == 6


def test_blank_delivery_token_becomes_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("CONTENT_FACTORY_DELIVERY_TOKEN", "   ")

    assert Settings().delivery_token is None


def test_delivery_retention_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("CONTENT_FACTORY_DELIVERY_RETENTION_HOURS", "0")

    with pytest.raises(ValidationError):
        Settings()


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
    for name in (CONFIG_DIR, DB_DIR, CONTENT_DIR, ARCHIVE_DIR, QUERIES_DIR):
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


def test_supplier_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_supplier_env(monkeypatch)

    settings = load_supplier_settings()

    assert settings.mcp_url == "http://127.0.0.1:8765"
    assert settings.profile_id == ""
    assert settings.max_video_duration == 60
    assert settings.max_videos_per_author == 1
    assert settings.proxy == ""
    assert settings.close_profile_after_crawl is True


def test_supplier_environment_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_supplier_env(monkeypatch)
    monkeypatch.setenv("SUPPLIER_PROFILE_ID", "profile-9")
    monkeypatch.setenv("SUPPLIER_PROXY", "socks5://proxy:1080")
    monkeypatch.setenv("SUPPLIER_MAX_VIDEO_DURATION", "120")
    monkeypatch.setenv("SUPPLIER_MAX_VIEWS", "5000")

    settings = load_supplier_settings()

    assert settings.profile_id == "profile-9"
    assert settings.proxy == "socks5://proxy:1080"
    assert settings.max_video_duration == 120
    assert settings.max_views == 5000


def test_supplier_duration_order_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_supplier_env(monkeypatch)
    monkeypatch.setenv("SUPPLIER_MIN_DURATION", "90")
    monkeypatch.setenv("SUPPLIER_MAX_VIDEO_DURATION", "60")

    with pytest.raises(ConfigError, match="настройки поставщика"):
        load_supplier_settings()


@pytest.mark.parametrize(
    ("minimum", "maximum", "label"),
    [
        ("SUPPLIER_SCROLLS_MIN", "SUPPLIER_SCROLLS_MAX", "SUPPLIER_SCROLLS"),
        (
            "SUPPLIER_QUERIES_PER_SESSION_MIN",
            "SUPPLIER_QUERIES_PER_SESSION_MAX",
            "SUPPLIER_QUERIES_PER_SESSION",
        ),
        ("SUPPLIER_PAUSE_SCROLL_MIN", "SUPPLIER_PAUSE_SCROLL_MAX", "SUPPLIER_PAUSE_SCROLL"),
        ("SUPPLIER_PAGE_SETTLE_MIN", "SUPPLIER_PAGE_SETTLE_MAX", "SUPPLIER_PAGE_SETTLE"),
        ("SUPPLIER_SESSION_BREAK_MIN", "SUPPLIER_SESSION_BREAK_MAX", "SUPPLIER_SESSION_BREAK"),
        (
            "SUPPLIER_CAPTCHA_COOLDOWN_MIN",
            "SUPPLIER_CAPTCHA_COOLDOWN_MAX",
            "SUPPLIER_CAPTCHA_COOLDOWN",
        ),
    ],
)
def test_supplier_range_pairs_are_checked(
    monkeypatch: pytest.MonkeyPatch,
    minimum: str,
    maximum: str,
    label: str,
) -> None:
    _clear_supplier_env(monkeypatch)
    monkeypatch.setenv(minimum, "20")
    monkeypatch.setenv(maximum, "10")

    with pytest.raises(ConfigError, match=label):
        load_supplier_settings()


def test_supplier_accepts_single_video_per_author(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_supplier_env(monkeypatch)
    monkeypatch.setenv("SUPPLIER_MAX_VIDEOS_PER_AUTHOR", "1")

    assert load_supplier_settings().max_videos_per_author == 1


def test_supplier_rejects_more_than_one_video_per_author(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_supplier_env(monkeypatch)
    monkeypatch.setenv("SUPPLIER_MAX_VIDEOS_PER_AUTHOR", "2")

    with pytest.raises(ConfigError, match="MAX_VIDEOS_PER_AUTHOR"):
        load_supplier_settings()


def test_supplier_rejects_proxy_with_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_supplier_env(monkeypatch)
    monkeypatch.setenv("SUPPLIER_PROXY", "http://user:secret@ho st:9")

    with pytest.raises(ConfigError, match="пробелы"):
        load_supplier_settings()


def test_supplier_accepts_proxy_with_surrounding_whitespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_supplier_env(monkeypatch)
    monkeypatch.setenv("SUPPLIER_PROXY", "  http://127.0.0.1:8080  ")

    assert load_supplier_settings().proxy.strip() == "http://127.0.0.1:8080"


def test_supplier_validation_hides_raw_values(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_supplier_env(monkeypatch)
    monkeypatch.setenv("SUPPLIER_MAX_VIEWS", "secret-value")

    with pytest.raises(ConfigError) as error:
        load_supplier_settings()

    message = str(error.value)
    assert "SUPPLIER_MAX_VIEWS" in message
    assert "secret-value" not in message


def test_supplier_rejects_unknown_proxy_scheme(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_supplier_env(monkeypatch)
    monkeypatch.setenv("SUPPLIER_PROXY", "ftp://proxy:21")

    with pytest.raises(ConfigError, match="должен начинаться"):
        load_supplier_settings()


def test_supplier_rejects_proxy_without_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_supplier_env(monkeypatch)
    monkeypatch.setenv("SUPPLIER_PROXY", "http:///path")

    with pytest.raises(ConfigError, match="хост"):
        load_supplier_settings()


def test_supplier_rejects_unparsable_proxy_port(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_supplier_env(monkeypatch)
    monkeypatch.setenv("SUPPLIER_PROXY", "http://proxy:not-a-port")

    with pytest.raises(ConfigError, match="порт"):
        load_supplier_settings()


def test_build_supplier_config_merges_env_runtime_and_paths(tmp_path: Path) -> None:
    supplier = SupplierSettings(
        mcp_url=" http://mcp:8765/ ",
        profile_id=" profile-1 ",
        bitbrowser_url=" http://bb:54529/ ",
        max_video_duration=90,
        proxy=" http://proxy:8080 ",
    )
    runtime = RuntimeConfig(
        run=RunConfig(
            batch=20,
            interval=900.0,
            max_duration=600.0,
            pause_between_languages=1.0,
        ),
        search=SearchConfig(max_queries=7, pause=2.0),
        filter=FilterConfig(max=11, pause=0.5),
        download=DownloadConfig(workers=3, pause=0.25),
        captcha=CaptchaConfig(streak=3, pause=1800.0, long_pause=21600.0),
        alerts=AlertsConfig(cooldown=3600.0),
        languages={"ru": LanguageConfig(enabled=True, stock_target=100)},
    )

    cfg = build_supplier_config(
        supplier,
        runtime,
        db_path=tmp_path / "db.sqlite3",
        content_dir=tmp_path / "content" / "ru",
        language="ru",
    )

    assert cfg.profile_id == "profile-1"
    assert cfg.bitbrowser_url == "http://bb:54529"
    assert cfg.max_duration == 90
    assert cfg.max_queries == 7
    assert cfg.search_pause == 2.0
    assert cfg.filter_pause == 0.5
    assert cfg.download_workers == 3
    assert cfg.download_pause == 0.25
    assert cfg.proxy == "http://proxy:8080"
    assert cfg.db_path == tmp_path / "db.sqlite3"
    assert cfg.content_dir == tmp_path / "content" / "ru"
    assert cfg.language == "ru"
