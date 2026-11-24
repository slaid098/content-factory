"""Shared builders for supplier tests."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from content_factory.config.settings import SupplierSettings
from content_factory.schemas.config.runtime import (
    AlertsConfig,
    CaptchaConfig,
    DeliveryConfig,
    DownloadConfig,
    FilterConfig,
    GeneratorConfig,
    LanguageConfig,
    RunConfig,
    RuntimeConfig,
    SearchConfig,
)
from content_factory.schemas.supplier.settings import SupplierConfig
from content_factory.schemas.supplier.video import Video, VideoMetadata


def make_config(tmp_path: Path, **overrides: object) -> SupplierConfig:
    """Build a supplier config with zero pauses and one worker."""
    base = SupplierConfig(
        profile_id="profile",
        bitbrowser_url="http://127.0.0.1:54529",
        max_views=1000,
        max_followers=500,
        min_duration=5,
        max_duration=60,
        min_age_days=5,
        max_queries=30,
        scrolls_min=0,
        scrolls_max=0,
        queries_per_session_min=1,
        queries_per_session_max=1,
        search_pause=0.0,
        pause_scroll_min=0.0,
        pause_scroll_max=0.0,
        page_settle_min=0.0,
        page_settle_max=0.0,
        session_break_min=0,
        session_break_max=0,
        captcha_cooldown_min=900,
        captcha_cooldown_max=1800,
        probe_workers=1,
        filter_pause=0.0,
        download_workers=1,
        download_pause=0.0,
        db_path=tmp_path / "app_data" / "db" / "db.sqlite3",
        content_dir=tmp_path / "app_data" / "content" / "ru",
        proxy="http://127.0.0.1:8080",
        language="ru",
    )
    return replace(base, **overrides)


def make_video(
    video_id: str = "111",
    author: str = "author",
    *,
    language: str = "ru",
    **overrides: object,
) -> Video:
    """Build a found video for one language."""
    base = Video(
        video_id=video_id,
        url=f"https://www.tiktok.com/@{author}/video/{video_id}",
        author=author,
        query="query",
        language=language,
    )
    return replace(base, **overrides)


def make_metadata(video: Video, **overrides: object) -> VideoMetadata:
    """Build metadata that passes the default supplier filters."""
    base = VideoMetadata(
        video_id=video.video_id,
        url=video.url,
        author_id=video.author,
        author_name=video.author,
        description="",
        views=100,
        followers=50,
        duration=10,
        is_aigc=False,
        aigc_description="",
        width=1080,
        height=1920,
        create_time=1_700_000_000,
    )
    return replace(base, **overrides)


def make_runtime(**overrides: object) -> RuntimeConfig:
    """Build runtime limits with zero pauses for fast tests."""
    base = RuntimeConfig(
        run=RunConfig(
            batch=20,
            interval=900.0,
            max_duration=600.0,
            pause_between_languages=0.0,
        ),
        search=SearchConfig(max_queries=10, pause=0.0),
        filter=FilterConfig(max=100, pause=0.0),
        download=DownloadConfig(workers=1, pause=0.0),
        captcha=CaptchaConfig(streak=3, pause=1800.0, long_pause=21600.0),
        alerts=AlertsConfig(cooldown=3600.0),
        languages={
            "ru": LanguageConfig(enabled=True, stock_target=100),
            "en": LanguageConfig(enabled=False, stock_target=50),
        },
        generator=GeneratorConfig(target=20, refill_at=2),
        delivery=DeliveryConfig(max_count=100),
    )
    return replace(base, **overrides)


def make_supplier_settings(**overrides: object) -> SupplierSettings:
    """Build supplier settings with zero waits and an explicit proxy."""
    base = SupplierSettings(
        mcp_url="http://127.0.0.1:8765/",
        profile_id="profile-1",
        bitbrowser_url="http://127.0.0.1:54529/",
        scrolls_min=0,
        scrolls_max=0,
        queries_per_session_min=100,
        queries_per_session_max=100,
        pause_scroll_min=0.0,
        pause_scroll_max=0.0,
        page_settle_min=0.0,
        page_settle_max=0.0,
        session_break_min=0,
        session_break_max=0,
        captcha_cooldown_min=1,
        captcha_cooldown_max=1,
        probe_workers=1,
        proxy="http://127.0.0.1:8080",
        headless=True,
        mcp_timeout=5,
        close_profile_after_crawl=True,
    )
    return base.model_copy(update=overrides)
