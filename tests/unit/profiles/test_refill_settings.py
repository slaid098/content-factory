"""Profile refill settings table in SQLite."""

from __future__ import annotations

import sqlite3

from content_factory.db.profiles import ensure_schema, get_refill, upsert_refill
from content_factory.schemas.profiles.refill import ProfileRefill

_NOW = "2026-11-24T12:00:00+00:00"


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    ensure_schema(connection)
    return connection


def test_missing_row_is_disabled_with_global_values() -> None:
    connection = _connection()

    try:
        assert get_refill(connection, "dating/profile_2") == ProfileRefill(False, None, None)
    finally:
        connection.close()


def test_upsert_stores_all_fields() -> None:
    connection = _connection()

    try:
        upsert_refill(
            connection,
            "dating/profile_2",
            ProfileRefill(True, 30, 5),
            _NOW,
        )

        assert get_refill(connection, "dating/profile_2") == ProfileRefill(True, 30, 5)
    finally:
        connection.close()


def test_null_overrides_are_preserved() -> None:
    connection = _connection()

    try:
        upsert_refill(
            connection,
            "dating/profile_2",
            ProfileRefill(True, None, None),
            _NOW,
        )

        assert get_refill(connection, "dating/profile_2") == ProfileRefill(True, None, None)
    finally:
        connection.close()


def test_zero_refill_at_is_stored_as_zero() -> None:
    connection = _connection()

    try:
        upsert_refill(
            connection,
            "dating/profile_2",
            ProfileRefill(True, None, 0),
            _NOW,
        )

        refill = get_refill(connection, "dating/profile_2")
        assert refill.refill_at == 0
    finally:
        connection.close()


def test_second_upsert_replaces_the_row() -> None:
    connection = _connection()

    try:
        upsert_refill(connection, "dating/profile_2", ProfileRefill(True, 30, 5), _NOW)
        upsert_refill(connection, "dating/profile_2", ProfileRefill(False, None, 1), _NOW)

        assert get_refill(connection, "dating/profile_2") == ProfileRefill(False, None, 1)
        rows = connection.execute("SELECT COUNT(*) FROM profile_settings").fetchone()
        assert rows[0] == 1
    finally:
        connection.close()


def test_schema_creation_is_idempotent() -> None:
    connection = sqlite3.connect(":memory:")

    try:
        ensure_schema(connection)
        ensure_schema(connection)
        upsert_refill(connection, "dating/profile_2", ProfileRefill(True, 1, 0), _NOW)
        assert get_refill(connection, "dating/profile_2").enabled is True
    finally:
        connection.close()


def test_updated_at_is_written() -> None:
    connection = _connection()

    try:
        upsert_refill(connection, "dating/profile_2", ProfileRefill(True, 1, 0), _NOW)

        row = connection.execute(
            "SELECT updated_at FROM profile_settings WHERE profile_id = ?",
            ("dating/profile_2",),
        ).fetchone()
        assert row[0] == _NOW
    finally:
        connection.close()
