from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from content_factory.db.connection import connect
from content_factory.db.runtime_state import save_state
from content_factory.schemas.config.runtime import LanguageConfig
from content_factory.schemas.state.state import RuntimeState
from content_factory.schemas.stock.stock import LanguageStock, StockSnapshot
from content_factory.services.stock.service import collect_stock, count_stock


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"video")


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
            LanguageStock(language="ru", stock=3, target=150, deficit=147),
            LanguageStock(language="en", stock=100, target=100, deficit=0),
        ),
    )

    assert snapshot.generated_at == moment
    assert snapshot.languages[0].deficit == 147


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
    moment = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    state = RuntimeState(
        last_production_at=moment - timedelta(hours=3),
        captcha_blocked_until=moment + timedelta(hours=1),
        captcha_streak=2,
        zero_runs=1,
        last_alert_at=None,
        last_alert_kind=None,
        updated_at=moment,
    )
    connection = connect(tmp_path / "db" / "db.sqlite3")
    try:
        save_state(connection, state)
    finally:
        connection.close()

    snapshot, loaded = collect_stock(tmp_path, _languages(), now=moment)

    assert snapshot.generated_at == moment
    assert snapshot.languages == (
        LanguageStock(language="en", stock=1, target=5, deficit=4),
        LanguageStock(language="ru", stock=2, target=10, deficit=8),
    )
    assert loaded.captcha_streak == 2
    assert loaded.zero_runs == 1
    assert loaded.captcha_blocked_until == state.captcha_blocked_until


def test_collect_stock_without_database_creates_nothing(tmp_path: Path) -> None:
    moment = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

    snapshot, state = collect_stock(tmp_path, _languages(), now=moment)

    assert snapshot.languages[0].stock == 0
    assert state.last_production_at is None
    assert state.captcha_streak == 0
    assert list(tmp_path.iterdir()) == []


def test_collect_stock_missing_content_directory_counts_zero(tmp_path: Path) -> None:
    moment = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

    snapshot, _ = collect_stock(tmp_path, _languages(), now=moment)

    assert [(row.language, row.stock, row.deficit) for row in snapshot.languages] == [
        ("en", 0, 5),
        ("ru", 0, 10),
    ]


def test_collect_stock_hides_disabled_languages(tmp_path: Path) -> None:
    moment = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)

    snapshot, _ = collect_stock(tmp_path, _languages(), now=moment)

    assert all(row.language != "de" for row in snapshot.languages)


def test_collect_stock_keeps_stock_above_target_at_zero_deficit(tmp_path: Path) -> None:
    _touch(tmp_path / "content" / "ru" / "a.mp4")
    _touch(tmp_path / "content" / "ru" / "b.mp4")
    _touch(tmp_path / "content" / "ru" / "c.mp4")
    languages = {"ru": LanguageConfig(enabled=True, stock_target=2)}

    snapshot, _ = collect_stock(
        tmp_path,
        languages,
        now=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
    )

    assert snapshot.languages[0].stock == 3
    assert snapshot.languages[0].deficit == 0
