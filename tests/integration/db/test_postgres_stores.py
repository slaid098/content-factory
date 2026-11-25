"""Store functions against PostgreSQL: stock, titles, video metadata, state."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from content_factory.db.connection import connect, connect_readonly, db_exists
from content_factory.db.profiles import get_refill, upsert_refill
from content_factory.db.runtime_state import (
    load_alert_times,
    load_state,
    save_alert_time,
    save_state,
)
from content_factory.db.stock import (
    count_by_profile,
    list_available,
    mark_delivered,
    prune_missing,
    upsert_item,
)
from content_factory.db.titles import load_titles, recent_titles, save_title
from content_factory.db.video_meta import load_video_meta
from content_factory.schemas.content.delivery import ContentType
from content_factory.schemas.content.video import VideoMeta
from content_factory.schemas.profiles.refill import ProfileRefill
from content_factory.schemas.state.state import RuntimeState
from content_factory.schemas.stock.layout import StockFile
from content_factory.schemas.supplier.results import FilterDecision
from content_factory.schemas.supplier.video import DownloadedVideo, Video, VideoMetadata
from content_factory.services.supplier.repository import Repository

_NOW = "2026-05-01T12:00:00+00:00"
_DELIVERED_AT = "2026-05-02T12:00:00+00:00"


def _item(
    path: str,
    *,
    language: str = "ru",
    niche: str = "dating",
    profile_id: str | None = "dating/profile_1",
    content_type: ContentType = ContentType.VIDEO,
) -> StockFile:
    return StockFile(niche, language, Path(path), profile_id, content_type)


def test_stock_upsert_list_mark_and_prune(pg: Path) -> None:
    connection = connect(pg)
    try:
        upsert_item(connection, _item("content/dating/ru/a.mp4"), 42, _NOW)
        upsert_item(
            connection,
            _item("content/dating/ru/b.jpg", content_type=ContentType.PHOTO),
            7,
            _NOW,
        )
        connection.commit()

        assert list_available(connection, "ru") == [("content/dating/ru/a.mp4", "dating/profile_1")]
        assert list_available(connection, "ru", content_type=ContentType.PHOTO) == [
            ("content/dating/ru/b.jpg", "dating/profile_1")
        ]
        assert count_by_profile(connection, "dating/profile_1") == 1

        mark_delivered(connection, ["content/dating/ru/a.mp4"], "request-1", _DELIVERED_AT)
        connection.commit()

        assert list_available(connection, "ru") == []
        assert count_by_profile(connection, "dating/profile_1") == 0
        assert prune_missing(connection, set()) == 1
        connection.commit()
    finally:
        connection.close()


def test_stock_upsert_keeps_delivery_history(pg: Path) -> None:
    connection = connect(pg)
    try:
        upsert_item(connection, _item("content/dating/ru/a.mp4"), 1, _NOW)
        mark_delivered(connection, ["content/dating/ru/a.mp4"], "request-1", _DELIVERED_AT)
        connection.commit()

        upsert_item(connection, _item("content/dating/ru/a.mp4"), 99, _NOW)
        connection.commit()

        row = connection.execute(
            "SELECT size, delivered_at, request_id FROM stock_items WHERE path = ?",
            ("content/dating/ru/a.mp4",),
        ).fetchone()
        assert row is not None
        assert row["size"] == 99
        assert row["delivered_at"] == _DELIVERED_AT
        assert row["request_id"] == "request-1"
    finally:
        connection.close()


def test_titles_conflicts_and_order(pg: Path) -> None:
    connection = connect(pg)
    try:
        assert save_title(connection, "a" * 64, "Первое утро", "первое", "2026-05-01")
        assert save_title(connection, "b" * 64, "Второе утро", "второе", "2026-05-02")
        assert save_title(connection, "c" * 64, "Третье утро", "третье", "2026-05-02") is True
        assert save_title(connection, "a" * 64, "Другое", "другое", "2026-05-03") is False
        assert save_title(connection, "d" * 64, "Четвёртое", "первое", "2026-05-03") is False
    finally:
        connection.close()

    assert load_titles(pg, ["a" * 64, "b" * 64]) == {
        "a" * 64: "Первое утро",
        "b" * 64: "Второе утро",
    }
    # Equal created_at values fall back to the descending SHA-256 tie-breaker.
    assert recent_titles(pg, limit=2) == ("Третье утро", "Второе утро")
    assert recent_titles(pg, limit=0) == ()


def test_profile_refill_settings(pg: Path) -> None:
    connection = connect(pg)
    try:
        assert get_refill(connection, "dating/profile_1") == ProfileRefill(False, None, None)
        upsert_refill(connection, "dating/profile_1", ProfileRefill(True, 30, 5, 10, 4), _NOW)
        connection.commit()
        assert get_refill(connection, "dating/profile_1") == ProfileRefill(True, 30, 5, 10, 4)
    finally:
        connection.close()


def test_runtime_state_and_alert_cooldowns(pg: Path) -> None:
    moments = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    state = RuntimeState.empty()
    connection = connect(pg)
    try:
        save_state(connection, state)
        assert load_state(connection).captcha_streak == 0
        save_alert_time(connection, "supplier_crash", moments)
        assert load_alert_times(connection) == {"supplier_crash": moments}
    finally:
        connection.close()


def test_video_meta_joins_downloads_and_videos(pg: Path) -> None:
    repo = Repository(pg)
    video = Video(
        video_id="video-1",
        url="https://example.test/video",
        author="alice",
        query="курятник",
        language="ru",
    )
    metadata = VideoMetadata(
        video_id="video-1",
        url=video.url,
        author_id="alice",
        author_name="Alice",
        description="Описание ролика",
        views=10,
        followers=5,
        duration=42,
        is_aigc=False,
        aigc_description="",
        width=1080,
        height=1920,
    )
    assert repo.add_found(video)
    repo.save_decision(video, metadata, FilterDecision(True))
    repo.save_download(
        DownloadedVideo(video=video, path="content/ru/a.mp4", sha256="a" * 64, size=10)
    )

    assert load_video_meta(pg, ["a" * 64]) == {
        "a" * 64: VideoMeta(description="Описание ролика", query="курятник", duration=42)
    }


def test_dsn_never_touches_the_sqlite_path(pg: Path) -> None:
    assert not pg.parent.exists()
    assert db_exists(pg) is True

    connection = connect(pg)
    try:
        assert connection.dialect == "postgres"
    finally:
        connection.close()

    readonly = connect_readonly(pg)
    try:
        assert readonly.dialect == "postgres"
    finally:
        readonly.close()

    assert not pg.parent.exists()
