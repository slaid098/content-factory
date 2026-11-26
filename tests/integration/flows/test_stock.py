from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from loguru import logger
from tests.unit.common.delivery_helpers import add_title
from tests.unit.common.profile_helpers import add_profile, add_profile_video

from content_factory.db.connection import connect
from content_factory.db.runtime_state import save_state
from content_factory.schemas.config.runtime import LanguageConfig
from content_factory.schemas.state.state import RuntimeState
from content_factory.schemas.stock.stock import GeneralStock, LanguageStock, StockSnapshot
from content_factory.services.stock import language_files, stock_files
from content_factory.services.stock.service import collect_stock, count_stock

_MOMENT = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def _touch(path: Path, data: bytes = b"video") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_counts_only_mp4_files_of_each_language(tmp_path: Path) -> None:
    content_dir = tmp_path / "content"
    _touch(content_dir / "ru" / "a.mp4")
    _touch(content_dir / "ru" / "b.mp4")
    _touch(content_dir / "ru" / "c.tmp")
    _touch(content_dir / "ru" / "d.part")
    _touch(content_dir / "ru" / "notes.txt")
    _touch(content_dir / "ru" / "nested" / "inside.mp4")
    (content_dir / "ru" / "directory.mp4").mkdir()

    counts = count_stock(content_dir, ["ru", "en"])

    assert counts == {"ru": 2, "en": 0}


def test_counts_files_of_two_niches_as_one_language(tmp_path: Path) -> None:
    content_dir = tmp_path / "content"
    _touch(content_dir / "ru" / "flat.mp4")
    _touch(content_dir / "general" / "ru" / "supplier.mp4")
    _touch(content_dir / "dating" / "ru" / "dating_profile_2_run_001.mp4")
    _touch(content_dir / "dating" / "en" / "english.mp4")

    counts = count_stock(content_dir, ["ru", "en", "de"])

    assert counts == {"ru": 3, "en": 1, "de": 0}


def test_mp4_files_are_regular_and_sorted_by_name(tmp_path: Path) -> None:
    content_dir = tmp_path / "content"
    directory = content_dir / "ru"
    _touch(directory / "b.mp4")
    _touch(directory / "a.mp4")
    _touch(directory / "c.txt")
    _touch(directory / "nested" / "inside.mp4")
    (directory / "z.mp4").mkdir()

    assert [item.path.name for item in language_files(content_dir, "ru")] == ["a.mp4", "b.mp4"]
    assert language_files(content_dir, "en") == []
    assert stock_files(tmp_path / "missing") == []


def test_missing_language_directory_counts_as_zero(tmp_path: Path) -> None:
    assert count_stock(tmp_path / "missing", ["ru"]) == {"ru": 0}


def test_empty_language_directory_counts_as_zero(tmp_path: Path) -> None:
    (tmp_path / "content" / "ru").mkdir(parents=True)

    assert count_stock(tmp_path / "content", ["ru"]) == {"ru": 0}


def test_snapshot_keeps_language_positions() -> None:
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    snapshot = StockSnapshot(
        generated_at=moment,
        languages=(
            LanguageStock(
                language="ru",
                stock=3,
                target=150,
                deficit=147,
                titled=2,
                untitled=1,
                video_bytes=30,
            ),
            LanguageStock(
                language="en",
                stock=100,
                target=100,
                deficit=0,
                titled=100,
                untitled=0,
                video_bytes=1000,
            ),
        ),
    )

    assert snapshot.generated_at == moment
    assert snapshot.languages[0].deficit == 147
    assert snapshot.languages[0].untitled == 1
    assert snapshot.languages[1].video_bytes == 1000


def _languages() -> dict[str, LanguageConfig]:
    return {
        "ru": LanguageConfig(enabled=True, stock_target=10),
        "en": LanguageConfig(enabled=True, stock_target=5),
        "de": LanguageConfig(enabled=False, stock_target=7),
    }


def test_collect_stock_reports_enabled_languages_and_state(tmp_path: Path) -> None:
    _touch(tmp_path / "content" / "ru" / "a.mp4")
    _touch(tmp_path / "content" / "ru" / "b.mp4")
    _touch(tmp_path / "content" / "en" / "c.mp4")
    _touch(tmp_path / "content" / "de" / "d.mp4")
    state = RuntimeState(
        last_production_at=_MOMENT - timedelta(hours=3),
        captcha_blocked_until=_MOMENT + timedelta(hours=1),
        captcha_streak=2,
        zero_runs=1,
        last_alert_at=None,
        last_alert_kind=None,
        updated_at=_MOMENT,
    )
    connection = connect(tmp_path / "db" / "db.sqlite3")
    try:
        save_state(connection, state)
    finally:
        connection.close()

    snapshot, loaded = collect_stock(tmp_path, _languages(), now=_MOMENT)

    assert snapshot.generated_at == _MOMENT
    assert snapshot.languages == (
        LanguageStock(
            language="en",
            stock=1,
            target=5,
            deficit=4,
            titled=0,
            untitled=1,
            video_bytes=5,
        ),
        LanguageStock(
            language="ru",
            stock=2,
            target=10,
            deficit=8,
            titled=0,
            untitled=2,
            video_bytes=10,
        ),
    )
    assert loaded.captcha_streak == 2
    assert loaded.zero_runs == 1
    assert loaded.captcha_blocked_until == state.captcha_blocked_until


def test_collect_stock_reports_general_videos_only(tmp_path: Path) -> None:
    _touch(tmp_path / "content" / "general" / "ru" / "a.mp4", b"12")
    _touch(tmp_path / "content" / "ru" / "b.mp4", b"345")
    _touch(tmp_path / "content" / "general" / "ru" / "post.jpg", b"photo")
    _touch(tmp_path / "content" / "general" / "de" / "d.mp4", b"disabled")
    add_profile(tmp_path)
    add_profile_video(tmp_path, filename="dating_profile_1_run_001.mp4")

    snapshot, _ = collect_stock(tmp_path, _languages(), now=_MOMENT)

    assert snapshot.general == (
        GeneralStock(language="en", stock=0, target=5),
        GeneralStock(language="ru", stock=2, target=10),
    )
    assert [row.language for row in snapshot.general] == [
        row.language for row in snapshot.languages
    ]
    assert [(row.language, row.stock) for row in snapshot.languages] == [
        ("en", 0),
        ("ru", 3),
    ]


def test_collect_stock_reports_titles_and_disk_usage(tmp_path: Path) -> None:
    _touch(tmp_path / "content" / "ru" / "a.mp4", b"12345")
    _touch(tmp_path / "content" / "ru" / "b.mp4", b"1234567")
    _touch(tmp_path / "content" / "ru" / "c.mp4", b"123")
    add_title(tmp_path / "db" / "db.sqlite3", "a", "Первое название")
    add_title(tmp_path / "db" / "db.sqlite3", "b", "Второе название")

    snapshot, _ = collect_stock(
        tmp_path,
        {"ru": LanguageConfig(enabled=True, stock_target=10)},
        now=_MOMENT,
    )

    row = snapshot.languages[0]
    assert row.stock == 3
    assert row.titled == 2
    assert row.untitled == 1
    assert row.video_bytes == 15


def test_collect_stock_counts_titles_and_bytes_across_niches(tmp_path: Path) -> None:
    _touch(tmp_path / "content" / "general" / "ru" / "a.mp4", b"12345")
    _touch(tmp_path / "content" / "dating" / "ru" / "b.mp4", b"1234567")
    add_title(tmp_path / "db" / "db.sqlite3", "a", "Название поставщика")

    snapshot, _ = collect_stock(
        tmp_path,
        {"ru": LanguageConfig(enabled=True, stock_target=10)},
        now=_MOMENT,
    )

    row = snapshot.languages[0]
    assert row.stock == 2
    assert row.titled == 1
    assert row.untitled == 1
    assert row.video_bytes == 12


def test_collect_stock_ignores_titles_without_files(tmp_path: Path) -> None:
    _touch(tmp_path / "content" / "ru" / "a.mp4")
    add_title(tmp_path / "db" / "db.sqlite3", "ghost", "Название удалённого файла")

    snapshot, _ = collect_stock(
        tmp_path,
        {"ru": LanguageConfig(enabled=True, stock_target=10)},
        now=_MOMENT,
    )

    row = snapshot.languages[0]
    assert row.stock == 1
    assert row.titled == 0
    assert row.untitled == 1


def test_collect_stock_without_titles_table_treats_files_as_untitled(tmp_path: Path) -> None:
    _touch(tmp_path / "content" / "ru" / "a.mp4", b"123")
    connection = connect(tmp_path / "db" / "db.sqlite3")
    try:
        save_state(connection, RuntimeState.empty())
    finally:
        connection.close()

    snapshot, _ = collect_stock(
        tmp_path,
        {"ru": LanguageConfig(enabled=True, stock_target=10)},
        now=_MOMENT,
    )

    row = snapshot.languages[0]
    assert row.titled == 0
    assert row.untitled == row.stock == 1
    assert row.video_bytes == 3


def test_collect_stock_with_broken_database_treats_files_as_untitled(tmp_path: Path) -> None:
    _touch(tmp_path / "content" / "ru" / "a.mp4", b"123")
    db_path = tmp_path / "db" / "db.sqlite3"
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"not a database")

    snapshot, _ = collect_stock(
        tmp_path,
        {"ru": LanguageConfig(enabled=True, stock_target=10)},
        now=_MOMENT,
    )

    row = snapshot.languages[0]
    assert row.titled == 0
    assert row.untitled == row.stock == 1
    assert row.video_bytes == 3


def test_collect_stock_without_database_creates_nothing(tmp_path: Path) -> None:
    snapshot, state = collect_stock(tmp_path, _languages(), now=_MOMENT)

    assert snapshot.languages[0].stock == 0
    assert snapshot.languages[0].titled == 0
    assert snapshot.languages[0].untitled == 0
    assert snapshot.languages[0].video_bytes == 0
    assert state.last_production_at is None
    assert state.captcha_streak == 0
    assert list(tmp_path.iterdir()) == []


def test_collect_stock_missing_content_directory_counts_zero(tmp_path: Path) -> None:
    snapshot, _ = collect_stock(tmp_path, _languages(), now=_MOMENT)

    assert [
        (row.language, row.stock, row.deficit, row.titled, row.untitled, row.video_bytes)
        for row in snapshot.languages
    ] == [
        ("en", 0, 5, 0, 0, 0),
        ("ru", 0, 10, 0, 0, 0),
    ]


def test_collect_stock_hides_disabled_languages(tmp_path: Path) -> None:
    _touch(tmp_path / "content" / "de" / "d.mp4")
    add_title(tmp_path / "db" / "db.sqlite3", "d", "Немецкое название")

    snapshot, _ = collect_stock(tmp_path, _languages(), now=_MOMENT)

    assert all(row.language != "de" for row in snapshot.languages)


def test_collect_stock_keeps_stock_above_target_at_zero_deficit(tmp_path: Path) -> None:
    _touch(tmp_path / "content" / "ru" / "a.mp4")
    _touch(tmp_path / "content" / "ru" / "b.mp4")
    _touch(tmp_path / "content" / "ru" / "c.mp4")
    languages = {"ru": LanguageConfig(enabled=True, stock_target=2)}

    snapshot, _ = collect_stock(tmp_path, languages, now=_MOMENT)

    row = snapshot.languages[0]
    assert row.stock == 3
    assert row.deficit == 0
    assert row.titled == 0
    assert row.untitled == 3
    assert row.video_bytes == 15


def test_collect_stock_does_not_count_whitespace_only_titles(tmp_path: Path) -> None:
    _touch(tmp_path / "content" / "ru" / "a.mp4", b"123")
    add_title(tmp_path / "db" / "db.sqlite3", "a", "   ", norm="whitespace")

    snapshot, _ = collect_stock(
        tmp_path,
        {"ru": LanguageConfig(enabled=True, stock_target=10)},
        now=_MOMENT,
    )

    row = snapshot.languages[0]
    assert row.stock == 1
    assert row.titled == 0
    assert row.untitled == 1
    assert row.video_bytes == 3


def test_stock_counts_only_videos_and_ignores_photos(tmp_path: Path) -> None:
    _touch(tmp_path / "content" / "ru" / "a.mp4", b"12345")
    _touch(tmp_path / "content" / "ru" / "b.jpg", b"photo-bytes")
    _touch(tmp_path / "content" / "dating" / "ru" / "c.png", b"more-photo")
    add_title(tmp_path / "db" / "db.sqlite3", "a", "Название видео")

    assert count_stock(tmp_path / "content", ["ru"]) == {"ru": 1}

    snapshot, _ = collect_stock(
        tmp_path,
        {"ru": LanguageConfig(enabled=True, stock_target=10)},
        now=_MOMENT,
    )

    row = snapshot.languages[0]
    assert row.stock == 1
    assert row.titled == 1
    assert row.untitled == 0
    assert row.video_bytes == 5


def test_collect_stock_treats_unreadable_database_path_as_untitled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _touch(tmp_path / "content" / "ru" / "a.mp4", b"123")
    db_path = tmp_path / "db" / "db.sqlite3"
    original_is_file = Path.is_file

    def _deny(self: Path, *args: object, **kwargs: object) -> bool:
        if self == db_path:
            raise PermissionError("permission denied")
        return original_is_file(self, *args, **kwargs)

    monkeypatch.setattr(Path, "is_file", _deny)

    snapshot, state = collect_stock(
        tmp_path,
        {"ru": LanguageConfig(enabled=True, stock_target=10)},
        now=_MOMENT,
    )

    row = snapshot.languages[0]
    assert row.stock == 1
    assert row.titled == 0
    assert row.untitled == 1
    assert state.last_production_at is None
    assert state.captcha_streak == 0
    assert state.zero_runs == 0
    assert not (tmp_path / "db").exists()


def test_collect_stock_keeps_file_whose_size_cannot_be_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _touch(tmp_path / "content" / "ru" / "good.mp4", b"1234")
    ghost = _touch(tmp_path / "content" / "ru" / "ghost.mp4", b"123456")
    original_stat = Path.stat

    def _deny_stat(self: Path, *args: object, **kwargs: object) -> object:
        if self == ghost:
            raise PermissionError("permission denied")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _deny_stat)
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)), level="WARNING")
    try:
        snapshot, _ = collect_stock(
            tmp_path,
            {"ru": LanguageConfig(enabled=True, stock_target=10)},
            now=_MOMENT,
        )
    finally:
        logger.remove(sink)

    row = snapshot.languages[0]
    assert row.stock == 2
    assert row.titled == 0
    assert row.untitled == 2
    assert row.video_bytes == 4
    assert "Не удалось прочитать размер" in "\n".join(messages)
