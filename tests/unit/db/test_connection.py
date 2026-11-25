"""The database port: DSN validation and placeholder translation."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from content_factory.config.settings import Settings
from content_factory.db.connection import _to_postgres, database_url, db_exists
from content_factory.exceptions.errors import ConfigError

_SRC = Path(__file__).resolve().parents[3] / "src" / "content_factory"
_SQL_PREFIXES = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "CREATE",
    "ALTER",
    "DROP",
    "WITH",
    "PRAGMA",
)
_SQL_LITERAL_WITH_QUESTION = re.compile(r"'[^']*\?[^']*'")


def test_placeholders_are_translated_for_postgres() -> None:
    assert _to_postgres("SELECT a FROM t WHERE b = ? AND c = ?") == (
        "SELECT a FROM t WHERE b = %s AND c = %s"
    )
    assert _to_postgres("SELECT 1") == "SELECT 1"


def test_sql_string_literals_have_no_question_marks() -> None:
    offenders: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            sql = node.value.lstrip()
            if not sql.upper().startswith(_SQL_PREFIXES):
                continue
            if _SQL_LITERAL_WITH_QUESTION.search(sql):
                offenders.append(f"{path.relative_to(_SRC)}:{node.lineno}")
    assert offenders == []


def test_settings_accept_postgres_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "CONTENT_FACTORY_DATABASE_URL",
        "postgresql://content_factory:secret@localhost:5432/content_factory",
    )
    settings = Settings()
    assert settings.database_url is not None
    assert settings.database_url.startswith("postgresql://")


def test_settings_reject_other_dsn_schemes() -> None:
    with pytest.raises(ConfigError, match="postgresql://"):
        Settings(database_url="sqlite:///app_data/db/db.sqlite3")


def test_blank_dsn_means_sqlite() -> None:
    assert Settings(database_url="").database_url is None


def test_database_url_reads_the_cached_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DATABASE_URL", "postgres://localhost/db")
    from content_factory.config.settings import get_settings

    get_settings.cache_clear()
    assert database_url() == "postgres://localhost/db"
    assert db_exists(Path("/definitely/missing/db.sqlite3")) is True


def test_db_exists_checks_the_sqlite_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CONTENT_FACTORY_DATABASE_URL", raising=False)
    from content_factory.config.settings import get_settings

    get_settings.cache_clear()
    db_path = tmp_path / "db" / "db.sqlite3"
    assert db_exists(db_path) is False
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"")
    assert db_exists(db_path) is True
