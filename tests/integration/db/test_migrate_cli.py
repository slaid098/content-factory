"""Command line of the migration runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from content_factory.config.settings import get_settings
from content_factory.db.connection import connect
from content_factory.db.migrate import main


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
