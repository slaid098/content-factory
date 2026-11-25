"""Profile refill settings table in SQLite."""

from __future__ import annotations

from collections.abc import Iterator
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
    assert get_refill(connection, "dating/profile_2") == ProfileRefill(False, None, None)


def test_upsert_stores_all_fields(connection: Connection) -> None:
    upsert_refill(connection, "dating/profile_2", ProfileRefill(True, 30, 5), _NOW)

    assert get_refill(connection, "dating/profile_2") == ProfileRefill(True, 30, 5)


def test_null_overrides_are_preserved(connection: Connection) -> None:
    upsert_refill(connection, "dating/profile_2", ProfileRefill(True, None, None), _NOW)

    assert get_refill(connection, "dating/profile_2") == ProfileRefill(True, None, None)


def test_zero_refill_at_is_stored_as_zero(connection: Connection) -> None:
    upsert_refill(connection, "dating/profile_2", ProfileRefill(True, None, 0), _NOW)

    assert get_refill(connection, "dating/profile_2").refill_at == 0


def test_second_upsert_replaces_the_row(connection: Connection) -> None:
    upsert_refill(connection, "dating/profile_2", ProfileRefill(True, 30, 5), _NOW)
    upsert_refill(connection, "dating/profile_2", ProfileRefill(False, None, 1), _NOW)

    assert get_refill(connection, "dating/profile_2") == ProfileRefill(False, None, 1)
    rows = connection.execute("SELECT COUNT(*) FROM profile_settings").fetchone()
    assert rows[0] == 1


def test_migration_creates_the_settings_table(connection: Connection) -> None:
    upsert_refill(connection, "dating/profile_2", ProfileRefill(True, 1, 0), _NOW)

    assert get_refill(connection, "dating/profile_2").enabled is True


def test_updated_at_is_written(connection: Connection) -> None:
    upsert_refill(connection, "dating/profile_2", ProfileRefill(True, 1, 0), _NOW)

    row = connection.execute(
        "SELECT updated_at FROM profile_settings WHERE profile_id = ?",
        ("dating/profile_2",),
    ).fetchone()
    assert row[0] == _NOW
