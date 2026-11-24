"""SQLite stock index: upsert, prune, availability and delivery history."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from content_factory.db.stock import (
    count_by_profile,
    ensure_schema,
    list_available,
    mark_delivered,
    prune_missing,
    upsert_item,
    upsert_items,
)
from content_factory.schemas.content.delivery import ContentType
from content_factory.schemas.stock.layout import StockFile

_NOW = "2026-05-01T12:00:00+00:00"
_DELIVERED_AT = "2026-05-02T12:00:00+00:00"

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
)
"""


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    ensure_schema(connection)
    return connection


def _item(
    path: str,
    *,
    language: str = "ru",
    niche: str = "dating",
    profile_id: str | None = "dating/profile_1",
    content_type: ContentType = ContentType.VIDEO,
) -> StockFile:
    return StockFile(niche, language, Path(path), profile_id, content_type)


def _row(connection: sqlite3.Connection, path: str) -> tuple[object, ...]:
    row = connection.execute(
        """
        SELECT language, niche, profile_id, size, created_at, delivered_at, request_id
        FROM stock_items WHERE path = ?
        """,
        (path,),
    ).fetchone()
    assert row is not None
    return tuple(row)


def test_upsert_item_inserts_a_row_with_relative_path() -> None:
    connection = _connection()
    try:
        upsert_item(connection, _item("content/dating/ru/a.mp4"), 42, _NOW)

        assert _row(connection, "content/dating/ru/a.mp4") == (
            "ru",
            "dating",
            "dating/profile_1",
            42,
            _NOW,
            None,
            None,
        )
    finally:
        connection.close()


def test_upsert_item_updates_disk_fields_and_keeps_delivery_history() -> None:
    connection = _connection()
    try:
        path = "content/dating/ru/a.mp4"
        upsert_item(connection, _item(path), 42, _NOW)
        mark_delivered(connection, [path], "request-1", _DELIVERED_AT)
        upsert_item(connection, _item(path, profile_id=None), 99, "2026-06-01T00:00:00+00:00")

        assert _row(connection, path) == (
            "ru",
            "dating",
            "dating/profile_1",
            99,
            _NOW,
            _DELIVERED_AT,
            "request-1",
        )
    finally:
        connection.close()


def test_upsert_items_is_idempotent_and_keeps_one_row_per_path() -> None:
    connection = _connection()
    try:
        rows = [
            (_item("content/general/ru/a.mp4", niche="general"), 1, _NOW),
            (_item("content/general/ru/b.mp4", niche="general"), 2, _NOW),
        ]
        upsert_items(connection, rows)
        upsert_items(connection, rows)

        count = connection.execute("SELECT COUNT(*) FROM stock_items").fetchone()
        assert count is not None and count[0] == 2
    finally:
        connection.close()


def test_prune_missing_removes_only_undelivered_absent_rows() -> None:
    connection = _connection()
    try:
        rows = [
            (_item("content/general/ru/keep.mp4", niche="general"), 1, _NOW),
            (_item("content/general/ru/delivered.mp4", niche="general"), 1, _NOW),
            (_item("content/general/ru/gone.mp4", niche="general"), 1, _NOW),
        ]
        upsert_items(connection, rows)
        mark_delivered(
            connection,
            ["content/general/ru/delivered.mp4"],
            "request-1",
            _DELIVERED_AT,
        )

        removed = prune_missing(connection, {"content/general/ru/keep.mp4"})

        assert removed == 1
        paths = [
            str(row[0])
            for row in connection.execute("SELECT path FROM stock_items ORDER BY path").fetchall()
        ]
        assert paths == [
            "content/general/ru/delivered.mp4",
            "content/general/ru/keep.mp4",
        ]
    finally:
        connection.close()


def test_list_available_filters_language_and_delivered_rows_in_path_order() -> None:
    connection = _connection()
    try:
        rows = [
            (_item("content/general/ru/b.mp4", niche="general"), 1, _NOW),
            (_item("content/dating/ru/a.mp4"), 1, _NOW),
            (_item("content/general/en/c.mp4", language="en", niche="general"), 1, _NOW),
            (_item("content/general/ru/done.mp4", niche="general"), 1, _NOW),
        ]
        upsert_items(connection, rows)
        mark_delivered(
            connection,
            ["content/general/ru/done.mp4"],
            "request-1",
            _DELIVERED_AT,
        )

        assert list_available(connection, "ru") == [
            ("content/dating/ru/a.mp4", "dating/profile_1"),
            ("content/general/ru/b.mp4", "dating/profile_1"),
        ]
        assert list_available(connection, "de") == []
    finally:
        connection.close()


def test_list_available_filters_by_profile_id() -> None:
    connection = _connection()
    try:
        rows = [
            (_item("content/dating/ru/a.mp4"), 1, _NOW),
            (_item("content/dating/ru/b.mp4", profile_id="dating/profile_2"), 1, _NOW),
            (_item("content/dating/ru/c.mp4", profile_id=None), 1, _NOW),
        ]
        upsert_items(connection, rows)

        assert list_available(connection, "ru", profile_id="dating/profile_1") == [
            ("content/dating/ru/a.mp4", "dating/profile_1"),
        ]
        assert list_available(connection, "ru", profile_id="dating/missing") == []
    finally:
        connection.close()


def test_list_available_filters_by_niche() -> None:
    connection = _connection()
    try:
        rows = [
            (_item("content/general/ru/a.mp4", niche="general", profile_id=None), 1, _NOW),
            (_item("content/dating/ru/b.mp4"), 1, _NOW),
            (_item("content/auto-parts/ru/c.mp4", niche="auto-parts", profile_id=None), 1, _NOW),
        ]
        upsert_items(connection, rows)

        assert list_available(connection, "ru", niche="general") == [
            ("content/general/ru/a.mp4", None),
        ]
        assert list_available(connection, "ru", niche="auto-parts") == [
            ("content/auto-parts/ru/c.mp4", None),
        ]
    finally:
        connection.close()


def test_list_available_unassigned_only_skips_profile_rows() -> None:
    connection = _connection()
    try:
        rows = [
            (_item("content/general/ru/a.mp4", niche="general", profile_id=None), 1, _NOW),
            (_item("content/general/ru/b.mp4", niche="general"), 1, _NOW),
            (_item("content/dating/ru/c.mp4", profile_id=None), 1, _NOW),
        ]
        upsert_items(connection, rows)

        assert list_available(connection, "ru", unassigned_only=True) == [
            ("content/dating/ru/c.mp4", None),
            ("content/general/ru/a.mp4", None),
        ]
    finally:
        connection.close()


def test_list_available_combines_niche_and_unassigned_filters() -> None:
    connection = _connection()
    try:
        rows = [
            (_item("content/general/ru/a.mp4", niche="general", profile_id=None), 1, _NOW),
            (_item("content/general/ru/b.mp4", niche="general"), 1, _NOW),
            (_item("content/dating/ru/c.mp4", profile_id=None), 1, _NOW),
        ]
        upsert_items(connection, rows)

        assert list_available(connection, "ru", niche="general", unassigned_only=True) == [
            ("content/general/ru/a.mp4", None),
        ]
    finally:
        connection.close()


def test_count_by_profile_counts_only_available_rows() -> None:
    connection = _connection()
    try:
        rows = [
            (_item("content/dating/ru/a.mp4"), 1, _NOW),
            (_item("content/dating/ru/b.mp4"), 1, _NOW),
            (_item("content/dating/ru/c.mp4"), 1, _NOW),
            (_item("content/dating/ru/d.mp4", profile_id=None), 1, _NOW),
            (
                _item("content/general/ru/e.mp4", niche="general", profile_id="general/profile_2"),
                1,
                _NOW,
            ),
        ]
        upsert_items(connection, rows)
        mark_delivered(connection, ["content/dating/ru/c.mp4"], "request-1", _DELIVERED_AT)

        assert count_by_profile(connection, "dating/profile_1") == 2
        assert count_by_profile(connection, "missing/profile") == 0
    finally:
        connection.close()


def test_count_by_profile_ignores_photos_by_default() -> None:
    connection = _connection()
    try:
        rows = [
            (_item("content/dating/ru/a.mp4"), 1, _NOW),
            (
                _item("content/dating/ru/b.jpg", content_type=ContentType.PHOTO),
                1,
                _NOW,
            ),
        ]
        upsert_items(connection, rows)

        assert count_by_profile(connection, "dating/profile_1") == 1
        assert count_by_profile(connection, "dating/profile_1", content_type=ContentType.PHOTO) == 1
    finally:
        connection.close()


def test_mark_delivered_records_the_request_of_every_path() -> None:
    connection = _connection()
    try:
        rows = [
            (_item("content/general/ru/a.mp4", niche="general"), 1, _NOW),
            (_item("content/general/ru/b.mp4", niche="general"), 1, _NOW),
        ]
        upsert_items(connection, rows)

        mark_delivered(
            connection,
            ["content/general/ru/a.mp4", "content/general/ru/b.mp4"],
            "request-2",
            _DELIVERED_AT,
        )

        for path in ("content/general/ru/a.mp4", "content/general/ru/b.mp4"):
            assert _row(connection, path)[5:] == (_DELIVERED_AT, "request-2")
    finally:
        connection.close()


def test_upsert_item_stores_the_content_type() -> None:
    connection = _connection()
    try:
        upsert_item(
            connection,
            _item("content/dating/ru/a.jpg", content_type=ContentType.PHOTO),
            42,
            _NOW,
        )

        row = connection.execute(
            "SELECT content_type FROM stock_items WHERE path = ?",
            ("content/dating/ru/a.jpg",),
        ).fetchone()
        assert row == ("photo",)
    finally:
        connection.close()


def test_list_available_filters_by_content_type() -> None:
    connection = _connection()
    try:
        rows = [
            (_item("content/dating/ru/a.mp4"), 1, _NOW),
            (
                _item("content/dating/ru/b.jpg", content_type=ContentType.PHOTO),
                1,
                _NOW,
            ),
        ]
        upsert_items(connection, rows)

        assert list_available(connection, "ru") == [
            ("content/dating/ru/a.mp4", "dating/profile_1"),
        ]
        assert list_available(connection, "ru", content_type=ContentType.PHOTO) == [
            ("content/dating/ru/b.jpg", "dating/profile_1"),
        ]
    finally:
        connection.close()


def test_ensure_schema_adds_content_type_to_a_legacy_table() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(_LEGACY_SCHEMA)
        connection.execute(
            """
            INSERT INTO stock_items (path, language, niche, profile_id, size, created_at)
            VALUES (?, ?, ?, NULL, 1, ?)
            """,
            ("content/general/ru/a.mp4", "ru", "general", _NOW),
        )
        connection.commit()

        ensure_schema(connection)

        row = connection.execute(
            "SELECT content_type FROM stock_items WHERE path = ?",
            ("content/general/ru/a.mp4",),
        ).fetchone()
        assert row == ("video",)
    finally:
        connection.close()


def test_ensure_schema_indexes_language_and_content_type() -> None:
    connection = _connection()
    try:
        names = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }
        sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name = ?",
            ("idx_stock_items_language_type_delivered",),
        ).fetchone()

        assert "idx_stock_items_language_delivered" not in names
        assert sql_row is not None
        assert "content_type" in str(sql_row[0])
    finally:
        connection.close()
