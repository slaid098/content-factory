from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from content_factory.db.connection import connect
from content_factory.db.runtime_state import ensure_schema, load_state, save_state
from content_factory.schemas.state.state import RuntimeState


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "db" / "db.sqlite3")
    yield connection
    connection.close()


def test_connect_creates_directory_and_enables_wal(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "db.sqlite3"

    connection = connect(db_path)
    try:
        assert db_path.is_file()
        assert connection.row_factory is sqlite3.Row
        mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"
    finally:
        connection.close()


def test_load_state_without_rows_returns_empty(connection: sqlite3.Connection) -> None:
    state = load_state(connection)

    assert state.last_production_at is None
    assert state.captcha_blocked_until is None
    assert state.captcha_streak == 0
    assert state.zero_runs == 0
    assert state.last_alert_at is None
    assert state.last_alert_kind is None
    assert state.updated_at.utcoffset() == timedelta(0)


def test_save_and_load_roundtrip(connection: sqlite3.Connection) -> None:
    state = RuntimeState(
        last_production_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        captcha_blocked_until=datetime(2026, 1, 2, 4, 0, tzinfo=UTC),
        captcha_streak=2,
        zero_runs=1,
        last_alert_at=datetime(2026, 1, 2, 5, 0, tzinfo=UTC),
        last_alert_kind="captcha_streak",
        updated_at=datetime(2020, 1, 1, tzinfo=UTC),
    )

    save_state(connection, state)
    loaded = load_state(connection)

    assert loaded.last_production_at == state.last_production_at
    assert loaded.captcha_blocked_until == state.captcha_blocked_until
    assert loaded.captcha_streak == 2
    assert loaded.zero_runs == 1
    assert loaded.last_alert_at == state.last_alert_at
    assert loaded.last_alert_kind == "captcha_streak"
    assert loaded.updated_at > state.updated_at
    assert loaded.updated_at.utcoffset() == timedelta(0)


def test_saving_empty_state_keeps_nulls(connection: sqlite3.Connection) -> None:
    save_state(connection, RuntimeState.empty())

    loaded = load_state(connection)

    assert loaded.last_production_at is None
    assert loaded.captcha_blocked_until is None
    assert loaded.last_alert_at is None
    assert loaded.last_alert_kind is None


def test_save_state_keeps_single_row(connection: sqlite3.Connection) -> None:
    save_state(connection, RuntimeState.empty())
    save_state(connection, RuntimeState.empty())

    count = connection.execute("SELECT COUNT(*) FROM runtime_state").fetchone()[0]

    assert count == 1


def test_ensure_schema_is_idempotent(connection: sqlite3.Connection) -> None:
    ensure_schema(connection)
    ensure_schema(connection)

    count = connection.execute("SELECT COUNT(*) FROM runtime_state").fetchone()[0]

    assert count == 0


def test_empty_state_has_zero_counters() -> None:
    state = RuntimeState.empty()

    assert state.captcha_streak == 0
    assert state.zero_runs == 0
    assert state.updated_at.utcoffset() == timedelta(0)
