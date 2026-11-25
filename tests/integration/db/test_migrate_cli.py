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


def test_cli_hides_a_query_password_behind_a_fragment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "CONTENT_FACTORY_DATABASE_URL",
        "postgresql://user@127.0.0.1:1/content_factory?sslmode=require#&password=100%secret",
    )
    get_settings.cache_clear()

    assert main() == 1

    error = capsys.readouterr().err
    assert "100%secret" not in error
    assert "100%25secret" not in error
    assert "фрагмент" in error


@pytest.mark.parametrize(
    "query",
    [
        "sslpassword=100%25secret",
        "%73slpassword=100%25secret",
        "password%3D100%25secret",
    ],
)
def test_cli_hides_password_like_query_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    query: str,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv(
        "CONTENT_FACTORY_DATABASE_URL",
        f"postgresql://user@127.0.0.1:1/content_factory?{query}",
    )
    get_settings.cache_clear()

    assert main() == 1

    error = capsys.readouterr().err
    assert query not in error
    assert "100%25secret" not in error
    assert "100%secret" not in error
    assert "query" in error


def test_redact_masks_userinfo_case_insensitively() -> None:
    assert _redact("POSTGRESQL://user:secret@example.test:5432/db") == (
        "postgresql://***@example.test:5432/db"
    )
    assert _redact("postgresql://user:secret@example.test:notaport/db") == (
        "postgresql://***@example.test/db"
    )
    assert _redact("/tmp/db.sqlite3") == "/tmp/db.sqlite3"
