"""Source pool repository on PostgreSQL: migration, cursor and missing rows."""

from __future__ import annotations

from pathlib import Path

from content_factory.db.connection import connect
from content_factory.db.sources import mark_missing, mark_used, select_next, upsert_import

_NOW = "2026-05-01T12:00:00+00:00"
_PROFILE = "dating/profile_1"


def _add(connection, name: str, sha: str, duration: float = 4.0) -> None:
    upsert_import(
        connection,
        language="ru",
        profile_id=_PROFILE,
        path=f"sources/ru/dating_profile_1/{name}.mp4",
        sha256=sha,
        size=100,
        duration=duration,
        now=_NOW,
    )


def test_migration_creates_both_pool_tables(pg: Path) -> None:
    connection = connect(pg)
    try:
        assert connection.has_table("source_items") is True
        assert connection.has_table("source_cursors") is True
        assert "duration" in connection.columns("source_items")
        assert "last_source_id" in connection.columns("source_cursors")
    finally:
        connection.close()


def test_selection_wraps_and_missing_rows_are_skipped(pg: Path) -> None:
    connection = connect(pg)
    try:
        _add(connection, "a", "a" * 64)
        _add(connection, "b", "b" * 64)
        _add(connection, "c", "c" * 64)
        connection.commit()

        assert [
            item.path.rsplit("/", 1)[-1] for item in select_next(connection, "ru", _PROFILE, 2)
        ] == [
            "a.mp4",
            "b.mp4",
        ]
        mark_used(connection, 2, _NOW)
        connection.commit()
        assert [item.id for item in select_next(connection, "ru", _PROFILE, 2)] == [3, 1]

        marked = mark_missing(connection, {"sources/ru/dating_profile_1/a.mp4"}, _NOW)
        connection.commit()
        assert [item.path.rsplit("/", 1)[-1] for item in marked] == ["b.mp4", "c.mp4"]
        assert [item.id for item in select_next(connection, "ru", _PROFILE, 2)] == [1]
    finally:
        connection.close()
