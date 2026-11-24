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
from content_factory.schemas.stock.layout import StockFile

_NOW = "2026-05-01T12:00:00+00:00"
_DELIVERED_AT = "2026-05-02T12:00:00+00:00"


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
) -> StockFile:
    return StockFile(niche, language, Path(path), profile_id)


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
