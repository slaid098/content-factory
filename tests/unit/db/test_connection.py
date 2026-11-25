"""The database port: DSN validation and placeholder translation."""

from __future__ import annotations

import ast
import re
import traceback
from pathlib import Path

import pytest

from content_factory.config.dsn import sanitize_database_text
from content_factory.config.settings import Settings, get_settings
from content_factory.db.connection import (
    _backend_key,
    _PostgresConnection,
    _to_postgres,
    connect,
    database_url,
    db_exists,
)
from content_factory.db.errors import DatabaseError
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
    assert settings.database_url.get_secret_value().startswith("postgresql://")


def test_settings_normalize_database_url() -> None:
    settings = Settings(database_url="  POSTGRESQL://user:secret@localhost:5432/db  ")
    assert settings.database_url is not None
    assert settings.database_url.get_secret_value() == (
        "postgresql://user:secret@localhost:5432/db"
    )


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://postgres://user:secret@localhost/db",
        "postgresql:///db",
        "postgresql://localhost",
        "postgresql://user:secret@localhost:notaport/db",
        "postgresql://user:secret@local host/db",
    ],
)
def test_settings_reject_malformed_dsn(value: str) -> None:
    with pytest.raises(ConfigError):
        Settings(database_url=value)


def test_settings_reject_password_in_query_without_echoing_it() -> None:
    with pytest.raises(ConfigError, match="userinfo") as info:
        Settings(database_url="postgresql://user@localhost/db?password=100%25secret")

    assert "100%25secret" not in str(info.value)
    assert "100%secret" not in str(info.value)


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://user@localhost/db#?dbname=prod",
        "postgresql://user@localhost/db#?host=prod.example",
        "postgresql://user@localhost/db#?service=prod",
        "postgresql://user@localhost/db#?password=100%25secret",
    ],
)
def test_settings_reject_dsn_fragments(value: str) -> None:
    with pytest.raises(ConfigError, match="фрагмент") as info:
        Settings(database_url=value)

    assert value not in str(info.value)


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://:5432/content_factory_test",
        "postgresql://user@/content_factory_test",
        "postgresql:///content_factory_test",
    ],
)
def test_settings_reject_dsn_without_host(value: str) -> None:
    with pytest.raises(ConfigError, match="хост"):
        Settings(database_url=value)


@pytest.mark.parametrize(
    "query",
    [
        "dbname=another",
        "host=prod.example",
        "hostaddr=10.0.0.1",
        "port=5555",
        "service=production",
    ],
)
def test_settings_reject_retargeting_query_parameters(query: str) -> None:
    with pytest.raises(ConfigError, match="query"):
        Settings(database_url=f"postgresql://user@localhost/db?{query}")


@pytest.mark.parametrize(
    "secret",
    ["100%secret", "100%25secret"],
)
def test_sanitize_masks_query_password_after_a_fragment(secret: str) -> None:
    dsn = f"postgresql://user@localhost/db?sslmode=require#&password={secret}"
    text = f'invalid percent-encoded token: "{secret}"; conninfo {dsn}'

    sanitized = sanitize_database_text(text, dsn)

    assert secret not in sanitized
    assert secret.replace("%25", "%") not in sanitized
    assert "***" in sanitized


def test_sanitize_masks_query_password_raw_and_decoded() -> None:
    dsn = "postgresql://user@localhost/db?password=100%25secret"
    text = f'invalid percent-encoded token: "{dsn}" raw=100%25secret decoded=100%secret'

    sanitized = sanitize_database_text(text, dsn)

    assert "100%25secret" not in sanitized
    assert "100%secret" not in sanitized


def test_settings_hide_database_url() -> None:
    settings = Settings(database_url="postgresql://user:topsecret@localhost:5432/db")

    assert "topsecret" not in repr(settings)
    assert "topsecret" not in settings.model_dump_json()
    assert settings.database_url is not None
    assert settings.database_url.get_secret_value().endswith("/db")


def test_settings_reject_other_dsn_schemes() -> None:
    with pytest.raises(ConfigError, match="postgresql://"):
        Settings(database_url="sqlite:///app_data/db/db.sqlite3")


def test_blank_dsn_means_sqlite() -> None:
    assert Settings(database_url="").database_url is None


def test_database_url_reads_the_cached_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DATABASE_URL", "postgres://localhost/db")
    get_settings.cache_clear()
    assert database_url() == "postgres://localhost/db"
    assert db_exists(Path("/definitely/missing/db.sqlite3")) is True


def test_backend_key_hashes_the_dsn(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(
        "CONTENT_FACTORY_DATABASE_URL",
        "postgresql://user:topsecret@localhost:5432/content_factory",
    )
    get_settings.cache_clear()

    key = _backend_key(tmp_path / "db" / "db.sqlite3")

    assert key.startswith("postgres:")
    assert "topsecret" not in key
    assert len(key.removeprefix("postgres:")) == 64


def test_connect_failure_hides_the_password(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    psycopg = pytest.importorskip("psycopg")
    dsn = "postgresql://user:topsecret@db.example:5432/content_factory"
    monkeypatch.setenv("CONTENT_FACTORY_DATABASE_URL", dsn)
    get_settings.cache_clear()
    captured: dict[str, object] = {}

    def fail(conninfo: str, **kwargs: object) -> None:
        captured.update(kwargs)
        raise psycopg.OperationalError(f'invalid connection string: "{conninfo}"')

    monkeypatch.setattr(psycopg, "connect", fail)

    with pytest.raises(DatabaseError) as info:
        connect(tmp_path / "db" / "db.sqlite3")

    message = str(info.value)
    assert "topsecret" not in message
    assert "postgresql://***@db.example:5432/content_factory" in message
    assert "host=db.example:5432" in message
    assert "dbname=content_factory" in message
    assert captured["connect_timeout"] == 10
    assert not (tmp_path / "db").exists()


def test_postgres_failure_has_no_raw_exception_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    psycopg = pytest.importorskip("psycopg")
    dsn = "postgresql://user:userinfo-secret@db.example:5432/content_factory?password=query-secret"

    def fail(*args: object, **kwargs: object) -> None:
        raise psycopg.OperationalError(f'invalid connection string: "{dsn}"')

    monkeypatch.setattr(psycopg, "connect", fail)

    failure: DatabaseError | None = None
    try:
        _PostgresConnection.open(dsn)
    except DatabaseError as ex:
        failure = ex
        formatted = traceback.format_exc()
    else:  # pragma: no cover - the fake driver always fails
        pytest.fail("ожидалась DatabaseError")
    assert failure is not None
    message = str(failure)
    for secret in ("userinfo-secret", "query-secret"):
        assert secret not in message
        assert secret not in formatted
    assert failure.__cause__ is None
    assert failure.__context__ is None


def test_postgres_failure_hides_a_query_password_after_a_fragment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    psycopg = pytest.importorskip("psycopg")
    dsn = "postgresql://user@localhost/db?sslmode=require#&password=100%secret"

    def fail(*args: object, **kwargs: object) -> None:
        raise psycopg.OperationalError('invalid percent-encoded token: "100%secret"')

    monkeypatch.setattr(psycopg, "connect", fail)

    failure: DatabaseError | None = None
    try:
        _PostgresConnection.open(dsn)
    except DatabaseError as ex:
        failure = ex
        formatted = traceback.format_exc()
    else:  # pragma: no cover - the fake driver always fails
        pytest.fail("ожидалась DatabaseError")
    assert failure is not None
    assert "100%secret" not in str(failure)
    assert "100%secret" not in formatted
    assert failure.__cause__ is None
    assert failure.__context__ is None


def test_db_exists_checks_the_sqlite_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("CONTENT_FACTORY_DATABASE_URL", raising=False)
    from content_factory.config.settings import get_settings

    get_settings.cache_clear()
    db_path = tmp_path / "db" / "db.sqlite3"
    assert db_exists(db_path) is False
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"")
    assert db_exists(db_path) is True
