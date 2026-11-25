"""Profile refill settings table in SQLite."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from importlib import import_module
from pathlib import Path

import pytest

from content_factory.db.connection import connect
from content_factory.db.profiles import get_refill, upsert_refill
from content_factory.schemas.database.port import Connection
from content_factory.schemas.profiles.refill import ProfileRefill

_NOW = "2026-11-24T12:00:00+00:00"


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[Connection]:
    connection = connect(tmp_path / "db" / "db.sqlite3")
    yield connection
    connection.close()


def test_missing_row_is_disabled_with_global_values(connection: Connection) -> None:
    stored = get_refill(connection, "dating/profile_2")

    assert stored == ProfileRefill(False, None, None)
    assert stored.photo_target == 0
    assert stored.photo_refill_at == 0


def test_upsert_stores_all_fields(connection: Connection) -> None:
    upsert_refill(connection, "dating/profile_2", ProfileRefill(True, 30, 5), _NOW)

    stored = get_refill(connection, "dating/profile_2")

    assert stored == ProfileRefill(True, 30, 5, 0, 0)


def test_upsert_stores_photo_fields(connection: Connection) -> None:
    upsert_refill(connection, "dating/profile_2", ProfileRefill(True, 30, 5, 10, 4), _NOW)

    assert get_refill(connection, "dating/profile_2") == ProfileRefill(True, 30, 5, 10, 4)


def test_null_overrides_are_preserved(connection: Connection) -> None:
    upsert_refill(connection, "dating/profile_2", ProfileRefill(True, None, None), _NOW)

    assert get_refill(connection, "dating/profile_2") == ProfileRefill(True, None, None)


def test_zero_refill_at_is_stored_as_zero(connection: Connection) -> None:
    upsert_refill(connection, "dating/profile_2", ProfileRefill(True, None, 0), _NOW)

    assert get_refill(connection, "dating/profile_2").refill_at == 0


def test_second_upsert_replaces_the_row(connection: Connection) -> None:
    upsert_refill(connection, "dating/profile_2", ProfileRefill(True, 30, 5, 8, 2), _NOW)
    upsert_refill(connection, "dating/profile_2", ProfileRefill(False, None, 1), _NOW)

    assert get_refill(connection, "dating/profile_2") == ProfileRefill(False, None, 1)
    rows = connection.execute("SELECT COUNT(*) FROM profile_settings").fetchone()
    assert rows[0] == 1


def test_migration_creates_the_settings_table(connection: Connection) -> None:
    upsert_refill(connection, "dating/profile_2", ProfileRefill(True, 1, 0), _NOW)

    assert get_refill(connection, "dating/profile_2").enabled is True


def test_migration_adds_the_photo_columns(connection: Connection) -> None:
    assert {"photo_target", "photo_refill_at"} <= connection.columns("profile_settings")


def test_photo_migration_is_idempotent(connection: Connection) -> None:
    migration = import_module("content_factory.db.migrations.0003_photo_refill")

    migration.apply(connection)
    migration.apply(connection)
    connection.commit()

    assert {"photo_target", "photo_refill_at"} <= connection.columns("profile_settings")


def test_migration_upgrades_a_legacy_settings_table(tmp_path: Path) -> None:
    path = tmp_path / "db" / "db.sqlite3"
    path.parent.mkdir(parents=True)
    legacy = sqlite3.connect(path)
    try:
        legacy.execute(
            "CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        legacy.executemany(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            [("0001", _NOW), ("0002", _NOW)],
        )
        legacy.execute(
            """
            CREATE TABLE profile_settings (
                profile_id TEXT PRIMARY KEY,
                refill_enabled INTEGER NOT NULL DEFAULT 0,
                target INTEGER,
                refill_at INTEGER,
                updated_at TEXT NOT NULL
            )
            """
        )
        legacy.execute(
            "INSERT INTO profile_settings VALUES (?, ?, ?, ?, ?)",
            ("dating/profile_2", 1, 5, 1, _NOW),
        )
        legacy.commit()
    finally:
        legacy.close()

    connection = connect(path)
    try:
        assert get_refill(connection, "dating/profile_2") == ProfileRefill(True, 5, 1, 0, 0)
    finally:
        connection.close()


def test_updated_at_is_written(connection: Connection) -> None:
    upsert_refill(connection, "dating/profile_2", ProfileRefill(True, 1, 0), _NOW)

    row = connection.execute(
        "SELECT updated_at FROM profile_settings WHERE profile_id = ?",
        ("dating/profile_2",),
    ).fetchone()
    assert row[0] == _NOW
