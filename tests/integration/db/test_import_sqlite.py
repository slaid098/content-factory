"""Import of a SQLite file into the configured database."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from content_factory.db.connection import connect, db_exists
from content_factory.db.import_sqlite import _BATCH_SIZE, import_sqlite, main

_LEGACY_SCHEMA = """
CREATE TABLE stock_items (
    path TEXT PRIMARY KEY,
    language TEXT NOT NULL,
    niche TEXT NOT NULL,
    profile_id TEXT,
    size INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    request_id TEXT
);
CREATE TABLE titles (
    sha256 TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    norm TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE profile_settings (
    profile_id TEXT PRIMARY KEY,
    refill_enabled INTEGER NOT NULL DEFAULT 0,
    target INTEGER,
    refill_at INTEGER,
    updated_at TEXT NOT NULL
);
CREATE TABLE runtime_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_production_at TEXT,
    captcha_blocked_until TEXT,
    captcha_streak INTEGER NOT NULL DEFAULT 0,
    zero_runs INTEGER NOT NULL DEFAULT 0,
    last_alert_at TEXT,
    last_alert_kind TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE alert_cooldowns (
    kind TEXT PRIMARY KEY,
    sent_at TEXT NOT NULL
);
CREATE TABLE videos (
    video_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    author TEXT NOT NULL,
    query TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'found',
    discovered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE downloads (
    video_id TEXT PRIMARY KEY REFERENCES videos(video_id),
    sha256 TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    downloaded_at TEXT NOT NULL
);
"""

_SHA = "a" * 64
_DELIVERED_AT = "2026-05-02T12:00:00+00:00"


def _make_source(path: Path) -> None:
    """Create a legacy SQLite file missing content_type and language columns."""
    with sqlite3.connect(path) as connection:
        connection.executescript(_LEGACY_SCHEMA)
        connection.execute(
            """
            INSERT INTO stock_items (path, language, niche, profile_id, size, created_at,
                                     delivered_at, request_id)
            VALUES (?, ?, ?, NULL, 10, ?, ?, ?)
            """,
            ("content/general/ru/a.mp4", "ru", "general", "2026-05-01", _DELIVERED_AT, "req-1"),
        )
        connection.execute(
            "INSERT INTO titles (sha256, title, norm, created_at) VALUES (?, ?, ?, ?)",
            (_SHA, "Название", "название", "2026-05-01"),
        )
        connection.execute(
            """
            INSERT INTO profile_settings (profile_id, refill_enabled, target, refill_at, updated_at)
            VALUES (?, 1, 30, 5, ?)
            """,
            ("dating/profile_1", "2026-05-01"),
        )
        connection.execute(
            """
            INSERT INTO runtime_state (id, captcha_streak, zero_runs, updated_at)
            VALUES (1, 2, 3, ?)
            """,
            ("2026-05-01",),
        )
        connection.execute(
            "INSERT INTO alert_cooldowns (kind, sent_at) VALUES (?, ?)",
            ("supplier_crash", "2026-05-01"),
        )
        connection.execute(
            """
            INSERT INTO videos (video_id, url, author, query, status, discovered_at, updated_at)
            VALUES (?, ?, ?, ?, 'downloaded', ?, ?)
            """,
            ("video-1", "https://example.test/video", "alice", "q", "2026-05-01", "2026-05-01"),
        )
        connection.execute(
            """
            INSERT INTO downloads (video_id, sha256, path, size, downloaded_at)
            VALUES (?, ?, ?, 10, ?)
            """,
            ("video-1", _SHA, "content/ru/a.mp4", "2026-05-01"),
        )


def _assert_target_rows(db_path: Path) -> None:
    connection = connect(db_path)
    try:
        stock = connection.execute(
            "SELECT content_type, delivered_at, request_id FROM stock_items WHERE path = ?",
            ("content/general/ru/a.mp4",),
        ).fetchone()
        assert stock is not None
        assert (stock["content_type"], stock["delivered_at"], stock["request_id"]) == (
            "video",
            _DELIVERED_AT,
            "req-1",
        )
        video = connection.execute(
            "SELECT language, status FROM videos WHERE video_id = ?", ("video-1",)
        ).fetchone()
        assert video is not None
        assert (video["language"], video["status"]) == ("", "downloaded")
        assert connection.execute("SELECT COUNT(*) FROM downloads").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM titles").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM profile_settings").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM runtime_state").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM alert_cooldowns").fetchone()[0] == 1
    finally:
        connection.close()


def test_import_between_sqlite_files_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path / "target"))
    source = tmp_path / "source.sqlite3"
    _make_source(source)

    counts = import_sqlite(source)
    assert counts == {
        "stock_items": 1,
        "titles": 1,
        "profile_settings": 1,
        "runtime_state": 1,
        "alert_cooldowns": 1,
        "videos": 1,
        "downloads": 1,
    }
    target = tmp_path / "target" / "db" / "db.sqlite3"
    _assert_target_rows(target)

    assert import_sqlite(source) == dict.fromkeys(counts, 0)
    _assert_target_rows(target)


def test_import_preserves_delivered_at_into_postgres(pg: Path, tmp_path: Path) -> None:
    source = tmp_path / "source.sqlite3"
    _make_source(source)

    counts = import_sqlite(source)

    assert counts == {
        "stock_items": 1,
        "titles": 1,
        "profile_settings": 1,
        "runtime_state": 1,
        "alert_cooldowns": 1,
        "videos": 1,
        "downloads": 1,
    }
    assert db_exists(pg) is True
    _assert_target_rows(pg)
    assert import_sqlite(source) == dict.fromkeys(counts, 0)
    _assert_target_rows(pg)


def test_cli_reports_a_missing_source(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--sqlite", str(tmp_path / "missing.sqlite3")]) == 1
    assert "импорт не выполнен" in capsys.readouterr().err


def test_cli_reports_an_empty_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path / "target"))
    source = tmp_path / "empty.sqlite3"
    sqlite3.connect(source).close()

    assert main(["--sqlite", str(source)]) == 1

    assert "нет таблиц" in capsys.readouterr().err
    assert not (tmp_path / "target" / "db" / "db.sqlite3").exists()


def test_import_rejects_the_default_database_before_migrating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CONTENT_FACTORY_DATABASE_URL", raising=False)
    db_path = tmp_path / "db" / "db.sqlite3"
    db_path.parent.mkdir(parents=True)
    _make_source(db_path)

    assert main(["--sqlite", str(db_path)]) == 1
    assert "тот же файл" in capsys.readouterr().err

    with sqlite3.connect(db_path) as connection:
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert "schema_migrations" not in tables


def test_import_reads_more_than_one_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path / "target"))
    source = tmp_path / "source.sqlite3"
    _make_source(source)
    total = _BATCH_SIZE + 8
    with sqlite3.connect(source) as connection:
        connection.executemany(
            "INSERT INTO titles (sha256, title, norm, created_at) VALUES (?, ?, ?, ?)",
            [
                (f"{index:064x}", f"Заголовок {index}", f"норма {index}", "2026-05-01")
                for index in range(total - 1)
            ],
        )

    counts = import_sqlite(source)

    assert counts["titles"] == total
    connection = connect(tmp_path / "target" / "db" / "db.sqlite3")
    try:
        assert connection.execute("SELECT COUNT(*) FROM titles").fetchone()[0] == total
    finally:
        connection.close()


def test_cli_failure_hides_the_destination_password(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path / "target"))
    monkeypatch.setenv(
        "CONTENT_FACTORY_DATABASE_URL",
        "postgresql://user:topsecret@127.0.0.1:1/content_factory",
    )
    source = tmp_path / "source.sqlite3"
    _make_source(source)

    assert main(["--sqlite", str(source)]) == 1

    error = capsys.readouterr().err
    assert "topsecret" not in error
    assert "host=127.0.0.1:1" in error
    assert "dbname=content_factory" in error
