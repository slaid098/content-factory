"""Source queue: migration and repository against SQLite and PostgreSQL."""

from __future__ import annotations

from collections.abc import Iterator
from importlib import import_module
from pathlib import Path

import pytest

from content_factory.config.settings import get_settings
from content_factory.constants import DB_DIR, DB_FILENAME
from content_factory.db.connection import connect
from content_factory.db.sources import (
    get_cursor,
    get_source,
    list_next_sources,
    list_sources,
    mark_missing,
    mark_used,
    set_cursor,
    upsert_source,
)
from content_factory.schemas.database.port import Connection
from content_factory.schemas.generator.uniqueizer import (
    SourceCandidate,
    SourceImportAction,
    SourceItem,
)

_NOW = "2026-05-01T12:00:00+00:00"
_LATER = "2026-05-02T12:00:00+00:00"
_FIRST = "sources/ru/dating_profile_1/a.mp4"
_SECOND = "sources/ru/dating_profile_1/b.mp4"
_THIRD = "sources/ru/dating_profile_1/c.mp4"


@pytest.fixture(params=["sqlite", "postgres"])
def connection(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> Iterator[Connection]:
    """Open the same tests against SQLite and, when configured, PostgreSQL."""
    if request.param == "postgres":
        db_path = request.getfixturevalue("pg")
    else:
        monkeypatch.delenv("CONTENT_FACTORY_DATABASE_URL", raising=False)
        get_settings.cache_clear()
        db_path = tmp_path / DB_DIR / DB_FILENAME
    store = connect(db_path)
    try:
        yield store
    finally:
        store.close()


def _candidate(
    path: str,
    *,
    sha256: str = "a" * 64,
    language: str = "ru",
    profile_id: str = "dating/profile_1",
    size: int = 100,
    duration: float | None = 12.5,
) -> SourceCandidate:
    return SourceCandidate(
        path=path,
        language=language,
        profile_id=profile_id,
        sha256=sha256,
        size=size,
        duration=duration,
    )


def test_migration_creates_the_source_tables(connection: Connection) -> None:
    assert connection.has_table("source_items") is True
    assert connection.has_table("source_cursors") is True
    assert connection.columns("source_items") == {
        "path",
        "language",
        "profile_id",
        "sha256",
        "size",
        "duration",
        "added_at",
        "used_at",
        "missing_at",
        "use_count",
    }
    assert connection.columns("source_cursors") == {
        "language",
        "profile_id",
        "last_path",
        "updated_at",
    }
    versions = connection.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    ).fetchall()
    assert [row[0] for row in versions] == ["0001", "0002"]


def test_second_migration_is_idempotent(tmp_path: Path) -> None:
    migration = import_module("content_factory.db.migrations.0002_sources")
    store = connect(tmp_path / DB_DIR / DB_FILENAME)
    try:
        migration.apply(store)
        migration.apply(store)
        store.commit()
    finally:
        store.close()


def test_upsert_adds_a_row(connection: Connection) -> None:
    action = upsert_source(connection, _candidate(_FIRST, size=100, duration=12.5), _NOW)
    connection.commit()

    assert action is SourceImportAction.ADDED
    assert get_source(connection, _FIRST) == SourceItem(
        path=_FIRST,
        language="ru",
        profile_id="dating/profile_1",
        sha256="a" * 64,
        size=100,
        duration=12.5,
        added_at=_NOW,
        used_at=None,
        missing_at=None,
        use_count=0,
    )


@pytest.mark.parametrize(
    "path",
    [
        "/etc/passwd",
        "../escape.mp4",
        "sources/ru/dating_profile_1/../../escape.mp4",
        "sources\\ru\\clip.mp4",
        "C:/Windows/system32",
        "./clip.mp4",
        "",
    ],
)
def test_source_paths_must_be_safe(connection: Connection, path: str) -> None:
    with pytest.raises(ValueError, match="путь"):
        upsert_source(connection, _candidate(path), _NOW)
    with pytest.raises(ValueError, match="путь"):
        get_source(connection, path)


def test_upsert_conflicts_with_a_path_of_another_profile(connection: Connection) -> None:
    upsert_source(connection, _candidate(_FIRST), _NOW)
    action = upsert_source(
        connection,
        _candidate(_FIRST, profile_id="dating/profile_2"),
        _LATER,
    )
    connection.commit()

    assert action is SourceImportAction.CONFLICT
    item = get_source(connection, _FIRST)
    assert item is not None
    assert item.profile_id == "dating/profile_1"
    assert list_sources(connection, "ru", "dating/profile_2") == []


def test_upsert_keeps_usage_of_the_same_content(connection: Connection) -> None:
    upsert_source(connection, _candidate(_FIRST), _NOW)
    mark_used(connection, _FIRST, _LATER)
    mark_used(connection, _FIRST, _LATER)
    connection.commit()

    action = upsert_source(connection, _candidate(_FIRST, size=200), _LATER)
    connection.commit()

    assert action is SourceImportAction.UNCHANGED
    item = get_source(connection, _FIRST)
    assert item is not None
    assert item.use_count == 2
    assert item.used_at == _LATER
    assert item.size == 200
    assert item.added_at == _NOW


def test_upsert_resets_usage_when_content_changes(connection: Connection) -> None:
    upsert_source(connection, _candidate(_FIRST), _NOW)
    mark_used(connection, _FIRST, _LATER)
    connection.commit()

    action = upsert_source(connection, _candidate(_FIRST, sha256="b" * 64), _LATER)
    connection.commit()

    assert action is SourceImportAction.UPDATED
    item = get_source(connection, _FIRST)
    assert item is not None
    assert item.sha256 == "b" * 64
    assert item.use_count == 0
    assert item.used_at is None


def test_upsert_returns_a_missing_source(connection: Connection) -> None:
    upsert_source(connection, _candidate(_FIRST), _NOW)
    mark_used(connection, _FIRST, _LATER)
    mark_missing(connection, [_FIRST], _LATER)
    connection.commit()

    action = upsert_source(connection, _candidate(_FIRST), _NOW)
    connection.commit()

    assert action is SourceImportAction.RETURNED
    item = get_source(connection, _FIRST)
    assert item is not None
    assert item.missing_at is None
    assert item.use_count == 1


def test_upsert_skips_a_duplicate_hash_of_the_profile(connection: Connection) -> None:
    upsert_source(connection, _candidate(_FIRST, sha256="c" * 64), _NOW)
    action = upsert_source(connection, _candidate(_SECOND, sha256="c" * 64), _NOW)
    connection.commit()

    assert action is SourceImportAction.DUPLICATE
    assert get_source(connection, _SECOND) is None


def test_next_sources_follow_the_cursor_and_skip_missing(connection: Connection) -> None:
    for path, sha in ((_FIRST, "a" * 64), (_SECOND, "b" * 64), (_THIRD, "c" * 64)):
        upsert_source(connection, _candidate(path, sha256=sha), _NOW)
    mark_missing(connection, [_SECOND], _LATER)
    connection.commit()

    assert [
        item.path for item in list_next_sources(connection, "ru", "dating/profile_1", None)
    ] == [_FIRST]
    assert [
        item.path for item in list_next_sources(connection, "ru", "dating/profile_1", _FIRST)
    ] == [_THIRD]
    assert list_next_sources(connection, "ru", "dating/profile_1", _THIRD) == []


def test_next_sources_reject_a_non_positive_limit(connection: Connection) -> None:
    with pytest.raises(ValueError, match="limit"):
        list_next_sources(connection, "ru", "dating/profile_1", None, limit=0)


def test_profiles_and_languages_are_isolated(connection: Connection) -> None:
    upsert_source(connection, _candidate(_FIRST, sha256="a" * 64), _NOW)
    upsert_source(
        connection,
        _candidate(
            "sources/ru/dating_profile_2/a.mp4",
            sha256="a" * 64,
            profile_id="dating/profile_2",
        ),
        _NOW,
    )
    upsert_source(
        connection,
        _candidate("sources/en/dating_profile_1/a.mp4", sha256="a" * 64, language="en"),
        _NOW,
    )
    connection.commit()

    assert [item.path for item in list_sources(connection, "ru", "dating/profile_1")] == [_FIRST]
    assert [item.path for item in list_sources(connection, "ru", "dating/profile_2")] == [
        "sources/ru/dating_profile_2/a.mp4"
    ]
    assert [item.path for item in list_sources(connection, "en", "dating/profile_1")] == [
        "sources/en/dating_profile_1/a.mp4"
    ]


def test_mark_used_reports_an_unknown_path(connection: Connection) -> None:
    assert mark_used(connection, _FIRST, _NOW) is False
    upsert_source(connection, _candidate(_FIRST), _NOW)
    assert mark_used(connection, _FIRST, _NOW) is True
    connection.commit()


def test_mark_missing_keeps_the_first_moment(connection: Connection) -> None:
    upsert_source(connection, _candidate(_FIRST), _NOW)
    connection.commit()

    assert mark_missing(connection, [_FIRST], _NOW) == 1
    assert mark_missing(connection, [_FIRST], _LATER) == 0
    connection.commit()

    item = get_source(connection, _FIRST)
    assert item is not None
    assert item.missing_at == _NOW


def test_cursor_is_read_updated_and_scoped(connection: Connection) -> None:
    assert get_cursor(connection, "ru", "dating/profile_1") is None

    set_cursor(connection, "ru", "dating/profile_1", _FIRST, _NOW)
    connection.commit()
    assert get_cursor(connection, "ru", "dating/profile_1") == _FIRST

    set_cursor(connection, "ru", "dating/profile_1", _SECOND, _LATER)
    connection.commit()
    assert get_cursor(connection, "ru", "dating/profile_1") == _SECOND
    assert get_cursor(connection, "ru", "dating/profile_2") is None
    assert get_cursor(connection, "en", "dating/profile_1") is None


def test_paths_are_listed_in_byte_order(connection: Connection) -> None:
    names = ("a.mp4", "ё.mp4", "я.mp4", "б.mp4")
    for index, name in enumerate(names):
        upsert_source(
            connection,
            _candidate(f"sources/ru/dating_profile_1/{name}", sha256=f"{index}" * 64),
            _NOW,
        )
    connection.commit()

    paths = [item.path for item in list_sources(connection, "ru", "dating/profile_1")]
    assert paths == [f"sources/ru/dating_profile_1/{name}" for name in sorted(names)]


def test_duration_may_be_unknown(connection: Connection) -> None:
    upsert_source(connection, _candidate(_FIRST, duration=None), _NOW)
    connection.commit()

    item = get_source(connection, _FIRST)
    assert item is not None
    assert item.duration is None
