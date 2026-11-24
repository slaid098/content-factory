"""Stock index synchronization with the content directory."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from tests.unit.common.delivery_helpers import touch_mp4

from content_factory.constants import CONTENT_DIR
from content_factory.services.stock import sync_stock


def _stock_rows(data_dir: Path) -> list[tuple[str, str, str, int, str, str | None, str | None]]:
    with sqlite3.connect(data_dir / "db" / "db.sqlite3") as connection:
        rows = connection.execute(
            """
            SELECT path, language, niche, size, created_at, delivered_at, request_id
            FROM stock_items ORDER BY path
            """
        ).fetchall()
    return [
        (
            str(row[0]),
            str(row[1]),
            str(row[2]),
            int(row[3]),
            str(row[4]),
            None if row[5] is None else str(row[5]),
            None if row[6] is None else str(row[6]),
        )
        for row in rows
    ]


def test_sync_stock_stores_relative_paths_and_is_idempotent(tmp_path: Path) -> None:
    dating = touch_mp4(
        tmp_path / CONTENT_DIR / "dating" / "ru" / "dating_profile_1_run_001.mp4",
        b"dating",
    )
    general = touch_mp4(tmp_path / CONTENT_DIR / "general" / "en" / ("b" * 64 + ".mp4"), b"general")

    assert sync_stock(tmp_path) == 2
    rows = _stock_rows(tmp_path)
    assert [row[0] for row in rows] == [
        "content/dating/ru/dating_profile_1_run_001.mp4",
        f"content/general/en/{'b' * 64}.mp4",
    ]
    assert [(row[1], row[2]) for row in rows] == [("ru", "dating"), ("en", "general")]
    assert [row[3] for row in rows] == [len(b"dating"), len(b"general")]
    assert all(row[4] and row[4].endswith("+00:00") for row in rows)
    assert all(row[5] is None and row[6] is None for row in rows)

    assert sync_stock(tmp_path) == 2
    assert _stock_rows(tmp_path) == rows
    assert dating.is_file()
    assert general.is_file()


def test_sync_stock_keeps_delivered_history_when_the_file_disappears(tmp_path: Path) -> None:
    source = touch_mp4(tmp_path / CONTENT_DIR / "general" / "ru" / "a.mp4", b"video")
    assert sync_stock(tmp_path) == 1
    with sqlite3.connect(tmp_path / "db" / "db.sqlite3") as connection:
        connection.execute(
            "UPDATE stock_items SET delivered_at = ?, request_id = ? WHERE path = ?",
            ("2026-05-02T12:00:00+00:00", "request-1", "content/general/ru/a.mp4"),
        )
        connection.commit()
    source.unlink()

    assert sync_stock(tmp_path) == 1
    rows = _stock_rows(tmp_path)
    assert rows[0][5:] == ("2026-05-02T12:00:00+00:00", "request-1")


def test_sync_stock_prunes_undelivered_rows_of_removed_files(tmp_path: Path) -> None:
    source = touch_mp4(tmp_path / CONTENT_DIR / "general" / "ru" / "a.mp4", b"video")
    assert sync_stock(tmp_path) == 1

    source.unlink()

    assert sync_stock(tmp_path) == 0
    assert _stock_rows(tmp_path) == []


def test_sync_stock_skips_unreadable_files_and_logs_a_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    log_records: list[dict[str, Any]],
) -> None:
    broken = touch_mp4(tmp_path / CONTENT_DIR / "general" / "ru" / "broken.mp4", b"broken")
    healthy = touch_mp4(tmp_path / CONTENT_DIR / "general" / "ru" / "healthy.mp4", b"healthy")
    original_stat = Path.stat

    def _deny_broken(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        if self == broken:
            raise PermissionError("permission denied")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", _deny_broken)

    assert sync_stock(tmp_path) == 1
    assert [row[0] for row in _stock_rows(tmp_path)] == ["content/general/ru/healthy.mp4"]
    assert healthy.is_file()
    records = [record for record in log_records if record["function"] == "_scan"]
    assert [record["level"].name for record in records] == ["WARNING"]
    assert records[0]["extra"]["path"] == str(broken)


def test_sync_stock_without_content_creates_an_empty_index(tmp_path: Path) -> None:
    assert not (tmp_path / "content").exists()
    assert not (tmp_path / "db" / "db.sqlite3").exists()

    assert sync_stock(tmp_path) == 0

    assert (tmp_path / "db" / "db.sqlite3").is_file()
    assert _stock_rows(tmp_path) == []
