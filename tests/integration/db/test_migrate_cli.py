"""Command line of the migration runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from content_factory.config.settings import get_settings
from content_factory.db.connection import connect
from content_factory.db.migrate import _redact, main


def test_cli_applies_migrations_and_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("CONTENT_FACTORY_DATABASE_URL", raising=False)
    get_settings.cache_clear()

    assert main() == 0
    assert "применено: 0001" in capsys.readouterr().out

    db_path = tmp_path / "db" / "db.sqlite3"
    assert db_path.is_file()
    connection = connect(db_path)
    try:
        versions = connection.execute("SELECT version FROM schema_migrations").fetchall()
        assert [row[0] for row in versions] == ["0001"]
    finally:
        connection.close()

    assert main() == 0
    assert "применено: 0001" in capsys.readouterr().out


def test_cli_failure_hides_the_destination_password(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "CONTENT_FACTORY_DATABASE_URL",
        "postgresql://user:topsecret@127.0.0.1:1/content_factory",
    )
    get_settings.cache_clear()

    assert main() == 1

    error = capsys.readouterr().err
    assert "topsecret" not in error
    assert "host=127.0.0.1:1" in error
    assert "dbname=content_factory" in error


def test_redact_masks_userinfo_case_insensitively() -> None:
    assert _redact("POSTGRESQL://user:secret@example.test:5432/db") == (
        "postgresql://***@example.test:5432/db"
    )
    assert _redact("postgresql://user:secret@example.test:notaport/db") == (
        "postgresql://***@example.test/db"
    )
    assert _redact("/tmp/db.sqlite3") == "/tmp/db.sqlite3"
