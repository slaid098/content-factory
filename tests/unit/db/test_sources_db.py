"""Source pool repository: import upserts, circular cursor and missing rows."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from content_factory.db.connection import connect
from content_factory.db.errors import IntegrityError
from content_factory.db.sources import mark_missing, mark_used, select_next, upsert_import
from content_factory.schemas.database.port import Connection
from content_factory.schemas.generator.source import SourceImportAction

_NOW = "2026-05-01T12:00:00+00:00"
_LATER = "2026-05-02T12:00:00+00:00"
_PROFILE = "dating/profile_1"


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[Connection]:
    connection = connect(tmp_path / "db" / "db.sqlite3")
    try:
        yield connection
    finally:
        connection.close()


def _path(name: str) -> str:
    return f"sources/ru/dating_profile_1/{name}.mp4"


def _add(
    connection: Connection,
    name: str,
    *,
    sha: str | None = None,
    duration: float = 4.0,
    language: str = "ru",
    profile_id: str = _PROFILE,
) -> SourceImportAction:
    return upsert_import(
        connection,
        language=language,
        profile_id=profile_id,
        path=_path(name),
        sha256=sha or name.ljust(64, "0"),
        size=100,
        duration=duration,
        now=_NOW,
    )


def _rows(connection: Connection) -> list[tuple[object, ...]]:
    rows = connection.execute(
        "SELECT id, path, sha256, used_at, use_count, missing_at FROM source_items ORDER BY id"
    ).fetchall()
    return [tuple(row) for row in rows]


def test_migration_creates_pool_tables(connection: Connection) -> None:
    assert connection.has_table("source_items") is True
    assert connection.has_table("source_cursors") is True
    assert {"path", "sha256", "duration", "used_at", "missing_at"} <= connection.columns(
        "source_items"
    )
    assert {"language", "profile_id", "last_source_id", "updated_at"} <= connection.columns(
        "source_cursors"
    )


def test_exported_file_is_added_with_metadata(connection: Connection) -> None:
    action = _add(connection, "a", duration=7.5)
    connection.commit()

    assert action is SourceImportAction.ADDED
    assert _rows(connection) == [(1, _path("a"), "a".ljust(64, "0"), None, 0, None)]


def test_same_path_and_hash_only_refreshes_metadata(connection: Connection) -> None:
    _add(connection, "a")
    connection.commit()
    connection.execute(
        "UPDATE source_items SET size = ?, duration = ? WHERE path = ?",
        (1, 1.0, _path("a")),
    )
    connection.commit()

    action = _add(connection, "a", duration=9.0)

    assert action is SourceImportAction.UNCHANGED
    row = connection.execute(
        "SELECT size, duration, missing_at FROM source_items WHERE path = ?", (_path("a"),)
    ).fetchone()
    assert row is not None
    assert tuple(row) == (100, 9.0, None)


def test_new_hash_at_same_path_resets_usage(connection: Connection) -> None:
    _add(connection, "a", sha="a" * 64)
    connection.commit()
    row = connection.execute("SELECT id FROM source_items").fetchone()
    assert row is not None
    mark_used(connection, int(row[0]), _NOW)
    connection.commit()

    action = _add(connection, "a", sha="b" * 64)

    assert action is SourceImportAction.UPDATED
    row = connection.execute(
        "SELECT sha256, used_at, use_count, missing_at, added_at FROM source_items"
    ).fetchone()
    assert row is not None
    assert tuple(row) == ("b" * 64, None, 0, None, _NOW)


def test_same_hash_at_another_path_is_a_duplicate(connection: Connection) -> None:
    _add(connection, "a", sha="a" * 64)
    connection.commit()

    action = _add(connection, "b", sha="a" * 64)
    connection.commit()

    assert action is SourceImportAction.DUPLICATE
    assert len(_rows(connection)) == 1


def test_missing_row_is_skipped_and_restored(connection: Connection) -> None:
    _add(connection, "a")
    _add(connection, "b")
    _add(connection, "c")
    connection.commit()

    marked = mark_missing(connection, {_path("a"), _path("c")}, _NOW)
    connection.commit()

    assert [item.path for item in marked] == [_path("b")]
    assert [item.id for item in select_next(connection, "ru", _PROFILE, 10)] == [1, 3]
    assert _add(connection, "b") is SourceImportAction.RESTORED
    assert [item.id for item in select_next(connection, "ru", _PROFILE, 10)] == [1, 2, 3]


def test_selection_follows_the_cursor_and_wraps(connection: Connection) -> None:
    _add(connection, "a")
    _add(connection, "b")
    _add(connection, "c")
    connection.commit()

    assert [item.id for item in select_next(connection, "ru", _PROFILE, 2)] == [1, 2]

    mark_used(connection, 2, _NOW)
    connection.commit()
    assert [item.id for item in select_next(connection, "ru", _PROFILE, 2)] == [3, 1]

    mark_used(connection, 1, _NOW)
    connection.commit()
    assert [item.id for item in select_next(connection, "ru", _PROFILE, 2)] == [2, 3]


def test_cursor_at_the_end_wraps_to_the_first_source(connection: Connection) -> None:
    _add(connection, "a")
    _add(connection, "b")
    connection.commit()
    mark_used(connection, 2, _NOW)
    connection.commit()

    assert [item.id for item in select_next(connection, "ru", _PROFILE, 2)] == [1, 2]


def test_selection_never_repeats_a_source_inside_one_batch(connection: Connection) -> None:
    _add(connection, "a")
    _add(connection, "b")
    _add(connection, "c")
    connection.commit()

    items = select_next(connection, "ru", _PROFILE, 3)

    assert [item.id for item in items] == [1, 2, 3]
    assert len({item.id for item in items}) == 3


def test_selection_returns_the_whole_short_pool(connection: Connection) -> None:
    _add(connection, "a")
    _add(connection, "b")
    connection.commit()

    items = select_next(connection, "ru", _PROFILE, 5)

    assert [item.id for item in items] == [1, 2]


def test_other_profile_and_language_are_not_selected(connection: Connection) -> None:
    _add(connection, "a")
    _add(connection, "b", language="en", profile_id="dating/profile_1")
    _add(connection, "c", profile_id="dating/profile_2")
    connection.commit()

    assert [item.path for item in select_next(connection, "ru", _PROFILE, 10)] == [_path("a")]
    assert select_next(connection, "ru", "general", 10) == ()


def test_reading_the_plan_does_not_move_the_cursor(connection: Connection) -> None:
    _add(connection, "a")
    _add(connection, "b")
    connection.commit()

    select_next(connection, "ru", _PROFILE, 1)

    assert connection.execute("SELECT COUNT(*) FROM source_cursors").fetchone()[0] == 0
    assert _rows(connection)[0][3:6] == (None, 0, None)


def test_mark_used_counts_reuses_and_rejects_an_unknown_row(connection: Connection) -> None:
    _add(connection, "a")
    connection.commit()

    mark_used(connection, 1, _NOW)
    mark_used(connection, 1, _LATER)
    connection.commit()

    row = connection.execute("SELECT used_at, use_count FROM source_items").fetchone()
    assert row is not None
    assert tuple(row) == (_LATER, 2)
    with pytest.raises(IntegrityError, match="id=99"):
        mark_used(connection, 99, _NOW)
