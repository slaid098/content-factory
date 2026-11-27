from __future__ import annotations

import multiprocessing
import os
import sqlite3
import stat
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from content_factory.db.connection import connect, connect_readonly
from content_factory.db.errors import OperationalError
from content_factory.db.lock import acquire, lock_path, locked
from content_factory.db.runtime_state import (
    load_alert_times,
    load_state,
    read_state,
    save_alert_time,
    save_state,
)
from content_factory.exceptions import LockError, LockTimeoutError, LockUnavailableError
from content_factory.schemas.database.port import Connection
from content_factory.schemas.state.state import RuntimeState


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[Connection]:
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


def test_connect_sets_busy_timeout_and_foreign_keys(tmp_path: Path) -> None:
    connection = connect(tmp_path / "db" / "db.sqlite3")
    try:
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    finally:
        connection.close()

    assert busy_timeout == 30_000
    assert foreign_keys == 1


def test_connect_passes_timeout_to_driver(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}
    real_connect = sqlite3.connect

    def spy(*args: object, **kwargs: object) -> sqlite3.Connection:
        captured.update(kwargs)
        return real_connect(*args, **kwargs)

    monkeypatch.setattr("content_factory.db.connection.sqlite3.connect", spy)
    connection = connect(tmp_path / "db" / "db.sqlite3")
    connection.close()

    assert captured["timeout"] == 30


def test_connect_closes_connection_when_setup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeConnection:
        closed = False

        def close(self) -> None:
            self.closed = True

    fake = FakeConnection()

    def fake_connect(*_args: object, **_kwargs: object) -> FakeConnection:
        return fake

    def fail_configure(_connection: object) -> None:
        raise RuntimeError("pragma failed")

    monkeypatch.setattr("content_factory.db.connection.sqlite3.connect", fake_connect)
    monkeypatch.setattr("content_factory.db.connection._configure", fail_configure)

    with pytest.raises(RuntimeError, match="pragma failed"):
        connect(tmp_path / "db" / "db.sqlite3")

    assert fake.closed


def test_connect_readonly_keeps_timeouts_and_row_factory(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "db.sqlite3"
    connect(db_path).close()

    readonly = connect_readonly(db_path)
    try:
        assert readonly.row_factory is sqlite3.Row
        assert readonly.execute("PRAGMA busy_timeout").fetchone()[0] == 30_000
    finally:
        readonly.close()


def test_load_state_without_rows_returns_empty(connection: Connection) -> None:
    state = load_state(connection)

    assert state.last_production_at is None
    assert state.captcha_blocked_until is None
    assert state.captcha_streak == 0
    assert state.zero_runs == 0
    assert state.last_alert_at is None
    assert state.last_alert_kind is None
    assert state.updated_at.utcoffset() == timedelta(0)


def test_save_and_load_roundtrip(connection: Connection) -> None:
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


def test_saving_empty_state_keeps_nulls(connection: Connection) -> None:
    save_state(connection, RuntimeState.empty())

    loaded = load_state(connection)

    assert loaded.last_production_at is None
    assert loaded.captcha_blocked_until is None
    assert loaded.last_alert_at is None
    assert loaded.last_alert_kind is None


def test_save_state_keeps_single_row(connection: Connection) -> None:
    save_state(connection, RuntimeState.empty())
    save_state(connection, RuntimeState.empty())

    count = connection.execute("SELECT COUNT(*) FROM runtime_state").fetchone()[0]

    assert count == 1


def test_migration_records_its_version_once(connection: Connection) -> None:
    versions = connection.execute("SELECT version FROM schema_migrations").fetchall()

    assert [row[0] for row in versions] == ["0001", "0002", "0003", "0004", "0005"]


def test_empty_state_has_zero_counters() -> None:
    state = RuntimeState.empty()

    assert state.captcha_streak == 0
    assert state.zero_runs == 0
    assert state.updated_at.utcoffset() == timedelta(0)


def test_read_state_without_schema_returns_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "db.sqlite3"
    db_path.parent.mkdir(parents=True)
    sqlite3.connect(db_path).close()

    connection = connect_readonly(db_path)
    try:
        state = read_state(connection)
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()

        assert state.last_production_at is None
        assert state.captcha_streak == 0
        assert tables == []
    finally:
        connection.close()


def test_connect_readonly_reads_existing_database(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "db.sqlite3"
    connection = connect(db_path)
    try:
        save_state(connection, RuntimeState.empty())
    finally:
        connection.close()

    readonly = connect_readonly(db_path)
    try:
        assert read_state(readonly).captcha_streak == 0
        with pytest.raises(OperationalError):
            readonly.execute("DELETE FROM runtime_state")
    finally:
        readonly.close()


def test_connect_readonly_missing_database_raises(tmp_path: Path) -> None:
    db_path = tmp_path / "missing" / "db.sqlite3"

    with pytest.raises(OperationalError):
        connect_readonly(db_path)

    assert not db_path.exists()


def test_lock_path_creates_private_lock_next_to_database(tmp_path: Path) -> None:
    path = lock_path(tmp_path / "db", "delivery")

    with locked(path, timeout=1):
        assert path.parent == tmp_path / "db"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_acquire_rejects_negative_timeout(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timeout"):
        acquire(lock_path(tmp_path / "db", "delivery"), timeout=-1)


def test_file_lock_release_is_idempotent(tmp_path: Path) -> None:
    lock = acquire(lock_path(tmp_path / "db", "delivery"), timeout=1)

    lock.release()
    lock.release()


def test_acquire_wraps_unusable_lock_path(tmp_path: Path) -> None:
    blocker = tmp_path / "db"
    blocker.write_text("not a directory", encoding="utf-8")

    with pytest.raises(LockError, match="lock-файл"):
        acquire(blocker / "delivery.lock", timeout=1)


def test_acquire_requires_fcntl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("content_factory.db.lock.fcntl", None)

    with pytest.raises(LockUnavailableError, match="fcntl"):
        acquire(lock_path(tmp_path / "db", "delivery"), timeout=1)


def test_different_locks_can_nest_without_deadlock(tmp_path: Path) -> None:
    build_slot = lock_path(tmp_path / "db", "delivery-build")
    archive_slot = lock_path(tmp_path / "db", "delivery")

    with locked(build_slot, timeout=1), locked(archive_slot, timeout=1):
        assert build_slot.is_file()
        assert archive_slot.is_file()


@pytest.mark.skipif(os.name != "posix", reason="flock is POSIX-only")
def test_lock_keeps_another_process_out(tmp_path: Path) -> None:
    path = lock_path(tmp_path / "db", "delivery")
    context = multiprocessing.get_context("fork")
    entered = context.Event()
    release = context.Event()

    def hold() -> None:
        with locked(path, timeout=5):
            entered.set()
            release.wait(10)

    process = context.Process(target=hold, daemon=True)
    process.start()
    try:
        assert entered.wait(10)
        with pytest.raises(LockTimeoutError), locked(path, timeout=0.2):
            pass
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.kill()
            process.join()
    assert process.exitcode == 0


def test_load_alert_times_without_rows_returns_empty(
    connection: Connection,
) -> None:
    assert load_alert_times(connection) == {}


def test_save_alert_time_upserts_per_kind(connection: Connection) -> None:
    first = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
    second = datetime(2026, 5, 1, 13, 0, tzinfo=UTC)

    save_alert_time(connection, "captcha_streak", first)
    save_alert_time(connection, "supplier_crash", first)
    save_alert_time(connection, "captcha_streak", second)

    assert load_alert_times(connection) == {
        "captcha_streak": second,
        "supplier_crash": first,
    }
