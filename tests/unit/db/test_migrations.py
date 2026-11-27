"""Transactions and cleanup of the migration runner."""

from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from content_factory.config.settings import get_settings
from content_factory.db import connection as connection_module
from content_factory.db.connection import connect
from content_factory.db.errors import DatabaseError, IntegrityError
from content_factory.db.migrations import runner
from content_factory.db.migrations.runner import applied_versions


class _EmptyCursor:
    """Cursor of a statement that returns no rows."""

    rowcount = 0

    def fetchone(self) -> None:
        return None

    def fetchall(self) -> list[object]:
        return []


class _FakePostgres:
    """Record the migration calls without a PostgreSQL server."""

    dialect = "postgres"

    def __init__(self, unlock_error: DatabaseError | None = None) -> None:
        self.events: list[str] = []
        self._unlock_error = unlock_error

    def execute(self, sql: str, params: object = ()) -> _EmptyCursor:
        if "pg_advisory_unlock" in sql:
            self.events.append("unlock")
            if self._unlock_error is not None:
                raise self._unlock_error
        elif "pg_advisory_lock" in sql:
            self.events.append("lock")
        return _EmptyCursor()

    def commit(self) -> None:
        self.events.append("commit")

    def rollback(self) -> None:
        self.events.append("rollback")

    def close(self) -> None:
        self.events.append("close")


def test_failed_migration_rolls_back_its_ddl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "db" / "db.sqlite3"
    connection = connect(db_path)
    try:

        def apply(broken: object) -> None:
            connection.execute("CREATE TABLE broken_probe (id INTEGER)")
            raise RuntimeError("boom")

        broken = SimpleNamespace(VERSION="9999", apply=apply)
        monkeypatch.setattr(runner, "_migrations", lambda: (broken,))

        with pytest.raises(RuntimeError, match="boom"):
            runner.apply_migrations(connection, db_path=db_path)

        assert connection.has_table("broken_probe") is False
        assert "9999" not in runner.applied_versions(connection)
    finally:
        connection.close()


def test_unlock_error_does_not_replace_the_original_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakePostgres(unlock_error=DatabaseError("unlock failed"))

    def fail(_connection: object) -> None:
        raise RuntimeError("original")

    monkeypatch.setattr(runner, "_apply_pending", fail)

    with pytest.raises(RuntimeError, match="original"):
        runner.apply_migrations(connection, db_path=Path("/unused/db.sqlite3"))

    assert connection.events.index("rollback") < connection.events.index("unlock")


def test_connect_closes_the_connection_when_migrations_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakePostgres()
    monkeypatch.setenv(
        "CONTENT_FACTORY_DATABASE_URL",
        "postgresql://user:topsecret@db.example/content_factory",
    )
    get_settings.cache_clear()
    monkeypatch.setattr(
        connection_module._PostgresConnection,
        "open",
        classmethod(lambda cls, dsn, readonly=False: fake),
    )

    def fail(_connection: object) -> None:
        raise RuntimeError("original")

    monkeypatch.setattr(runner, "_apply_pending", fail)

    with pytest.raises(RuntimeError, match="original"):
        connection_module.connect(Path("/unused/db.sqlite3"))

    assert fake.events[-1] == "close"
    assert "unlock" in fake.events


def test_generator_servers_migration_is_idempotent_and_keeps_contract_unique(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "db" / "db.sqlite3"
    connection = connect(db_path)
    try:
        assert connection.has_table("generator_servers")
        assert "0005" in applied_versions(connection)
        assert connection.columns("generator_servers") == {
            "id",
            "run_id",
            "profile",
            "status",
            "contract_id",
            "ip",
            "port",
            "gpu",
            "image",
            "cost_per_hour",
            "created_at",
            "updated_at",
            "destroyed_at",
            "last_error",
        }

        migration = importlib.import_module("content_factory.db.migrations.0005_generator_servers")
        migration.apply(connection)
        migration.apply(connection)

        insert = (
            "INSERT INTO generator_servers (id, status, created_at, updated_at, contract_id) "
            "VALUES (?, ?, ?, ?, ?)"
        )
        connection.execute(insert, ("s1", "renting", "t0", "t0", None))
        connection.execute(insert, ("s2", "renting", "t0", "t0", None))
        connection.execute(insert, ("s3", "ready", "t0", "t0", "c1"))
        with pytest.raises(IntegrityError):
            connection.execute(insert, ("s4", "ready", "t0", "t0", "c1"))
    finally:
        connection.close()
