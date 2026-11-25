"""Database access for operational state.

SQLite remains the dev/test backend, while PostgreSQL serves production when
``CONTENT_FACTORY_DATABASE_URL`` is set. This module is the single place that
imports the database drivers: every SQL statement of the service goes through
the small :class:`~content_factory.schemas.database.port.Connection` port.

The API and the Prefect worker are separate processes sharing ``app_data``, so
a SQLite connection keeps the same busy timeout, WAL mode and row factory as
before. Schema migrations run once per process on the first connection; a
file lock on SQLite and a session advisory lock on PostgreSQL keep two
processes from applying the same migration twice.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, cast

from content_factory.config.dsn import (
    database_target_label,
    normalize_database_url,
    sanitize_database_text,
)
from content_factory.db import errors
from content_factory.exceptions.errors import ConfigError
from content_factory.schemas.database.port import Connection, Cursor, Params, Row, Rows

_TIMEOUT_SECONDS = 30
_BUSY_TIMEOUT_MS = 30_000
_POSTGRES_LOCK_TIMEOUT = "30s"
_POSTGRES_CONNECT_TIMEOUT = 10
_TABLE_NAME_PATTERN = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"

_ready: set[str] = set()


def connect(db_path: Path) -> Connection:
    """Open the operational database for writing and apply migrations.

    Args:
        db_path: Path to the dev SQLite database. In PostgreSQL mode the path
            is neither created nor checked, because the DSN decides the target.

    Returns:
        A writable connection with the backend-specific settings applied.

    Raises:
        ConfigError: ``CONTENT_FACTORY_DATABASE_URL`` uses another scheme.
        DatabaseError: The database is unavailable or a migration fails.
    """
    dsn = database_url()
    connection = (
        _PostgresConnection.open(dsn) if dsn is not None else _SqliteConnection.open(db_path)
    )
    try:
        _apply_migrations_once(connection, db_path)
    except BaseException:
        connection.close()
        raise
    return connection


def connect_readonly(db_path: Path) -> Connection:
    """Open an existing database without creating or changing it.

    Args:
        db_path: Path to an existing dev SQLite database. In PostgreSQL mode
            the path is ignored.

    Returns:
        A read-only connection with the same timeouts as :func:`connect`.

    Raises:
        OperationalError: The SQLite file is missing or unreadable.
        DatabaseError: The PostgreSQL database cannot be opened.
    """
    dsn = database_url()
    if dsn is None:
        return _SqliteConnection.open(db_path, readonly=True)
    _ensure_postgres_migrations(db_path, dsn)
    return _PostgresConnection.open(dsn, readonly=True)


def db_exists(db_path: Path) -> bool:
    """Return whether the operational database can be read.

    Args:
        db_path: Path to the dev SQLite database.

    Returns:
        ``True`` when a PostgreSQL DSN is configured or the SQLite file exists.
    """
    if database_url() is not None:
        return True
    return db_path.is_file()


def database_url() -> str | None:
    """Return the validated PostgreSQL DSN, or ``None`` for SQLite.

    Raises:
        ConfigError: The DSN is not a PostgreSQL URL with a host and a
            database name.
    """
    from content_factory.config.settings import get_settings

    value = get_settings().database_url
    if value is None:
        return None
    return normalize_database_url(value.get_secret_value())


def sanitize_error_text(text: str) -> str:
    """Mask the configured database credentials in an error message."""
    try:
        dsn = database_url()
    except (ConfigError, ValueError):
        dsn = None
    return sanitize_database_text(text, dsn)


def _apply_migrations_once(connection: Connection, db_path: Path) -> None:
    """Apply pending migrations once per process for this backend."""
    key = _backend_key(db_path)
    if key in _ready:
        return
    from content_factory.db.migrations.runner import apply_migrations

    apply_migrations(connection, db_path=db_path)
    _ready.add(key)


def _ensure_postgres_migrations(db_path: Path, dsn: str) -> None:
    """Apply PostgreSQL migrations before a read-only connection is opened."""
    key = _backend_key(db_path)
    if key in _ready:
        return
    connection = _PostgresConnection.open(dsn)
    try:
        _apply_migrations_once(connection, db_path)
    finally:
        connection.close()


def _backend_key(db_path: Path) -> str:
    """Identify the backend and target for the per-process migration cache."""
    dsn = database_url()
    if dsn is not None:
        digest = hashlib.sha256(dsn.encode("utf-8")).hexdigest()
        return f"postgres:{digest}"
    return f"sqlite:{db_path.resolve()}"


def _translate_sqlite(error: sqlite3.Error) -> errors.DatabaseError:
    """Translate a driver error into the shared database error."""
    if isinstance(error, sqlite3.IntegrityError):
        return errors.IntegrityError(str(error))
    if isinstance(error, sqlite3.OperationalError):
        return errors.OperationalError(str(error))
    return errors.DatabaseError(str(error))


def _translate_postgres(error: BaseException, *, dsn: str | None = None) -> errors.DatabaseError:
    """Translate a psycopg error into the shared database error.

    Connection failures get a message built from the safe host, database name
    and SQLSTATE; every other message is stripped of credentials the driver
    may have echoed.
    """
    psycopg = _psycopg()
    message = (
        _connection_message(error, dsn) if dsn is not None else sanitize_database_text(str(error))
    )
    if isinstance(error, psycopg.errors.IntegrityError):
        return errors.IntegrityError(message)
    if isinstance(error, (psycopg.OperationalError, psycopg.InterfaceError)):
        return errors.OperationalError(message)
    return errors.DatabaseError(message)


def _connection_message(error: BaseException, dsn: str) -> str:
    """Build a connection failure message without the raw conninfo."""
    target = database_target_label(dsn)
    sqlstate = getattr(error, "sqlstate", None)
    state = f", sqlstate={sqlstate}" if sqlstate else ""
    detail = sanitize_database_text(str(error), dsn)
    return f"не удалось подключиться к PostgreSQL ({target}{state}): {detail}"


def _psycopg() -> Any:
    """Import psycopg lazily so SQLite-only runs do not need a server."""
    try:
        import psycopg
    except ImportError as ex:  # pragma: no cover - dependency is declared
        raise errors.OperationalError("для PostgreSQL нужен пакет psycopg") from ex
    return psycopg


def _run_postgres[T](operation: Callable[[], T], *, dsn: str | None = None) -> T:
    """Run a driver call and translate its error outside the except block.

    Raising after the ``except`` block keeps the raw psycopg exception out of
    ``__cause__``, ``__context__`` and the traceback, where it could print the
    DSN even when the translated message is sanitized.
    """
    psycopg = _psycopg()
    failure: errors.DatabaseError | None = None
    try:
        return operation()
    except psycopg.Error as ex:
        failure = _translate_postgres(ex, dsn=dsn)
    assert failure is not None
    raise failure


def _validate_table(table: str) -> None:
    """Reject table names that cannot be safely interpolated into a query."""
    if not table or any(char not in _TABLE_NAME_PATTERN for char in table):
        raise ValueError(f"недопустимое имя таблицы: {table!r}")


class _SqliteCursor:
    """Wrap a sqlite cursor without exposing the driver to callers."""

    def __init__(self, cursor: sqlite3.Cursor) -> None:
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def fetchone(self) -> Row | None:
        try:
            return cast(Row | None, self._cursor.fetchone())
        except sqlite3.Error as ex:
            raise _translate_sqlite(ex) from ex

    def fetchall(self) -> list[Row]:
        try:
            return list(self._cursor.fetchall())
        except sqlite3.Error as ex:
            raise _translate_sqlite(ex) from ex


class _SqliteConnection:
    """SQLite adapter with the pragmas used by the service."""

    dialect = "sqlite"

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @classmethod
    def open(cls, db_path: Path, *, readonly: bool = False) -> _SqliteConnection:
        """Open a writable or read-only SQLite connection.

        Raises:
            OperationalError: SQLite cannot open the file.
        """
        if readonly:
            _ensure_sqlite_file(db_path)
            uri = f"{db_path.resolve().as_uri()}?mode=ro"
            try:
                connection = sqlite3.connect(uri, uri=True, timeout=_TIMEOUT_SECONDS)
            except sqlite3.Error as ex:
                raise _translate_sqlite(ex) from ex
            try:
                _configure(connection)
            except sqlite3.Error as ex:
                connection.close()
                raise _translate_sqlite(ex) from ex
            except BaseException:
                connection.close()
                raise
            return cls(connection)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            connection = sqlite3.connect(db_path, timeout=_TIMEOUT_SECONDS)
        except sqlite3.Error as ex:
            raise _translate_sqlite(ex) from ex
        try:
            _configure(connection)
            connection.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error as ex:
            connection.close()
            raise _translate_sqlite(ex) from ex
        except BaseException:
            connection.close()
            raise
        return cls(connection)

    @property
    def row_factory(self) -> Any:
        """Return the underlying row factory for compatibility checks."""
        return self._connection.row_factory

    def execute(self, sql: str, params: Params = ()) -> Cursor:
        try:
            cursor = (
                self._connection.execute(sql, params) if params else self._connection.execute(sql)
            )
        except sqlite3.Error as ex:
            raise _translate_sqlite(ex) from ex
        return _SqliteCursor(cursor)

    def executemany(self, sql: str, params: Rows) -> Cursor:
        try:
            cursor = self._connection.executemany(sql, params)
        except sqlite3.Error as ex:
            raise _translate_sqlite(ex) from ex
        return _SqliteCursor(cursor)

    def begin(self) -> None:
        """Start an immediate transaction so DDL can be rolled back."""
        if self._connection.in_transaction:
            return
        try:
            self._connection.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as ex:
            raise _translate_sqlite(ex) from ex

    def commit(self) -> None:
        try:
            self._connection.commit()
        except sqlite3.Error as ex:
            raise _translate_sqlite(ex) from ex

    def rollback(self) -> None:
        try:
            self._connection.rollback()
        except sqlite3.Error as ex:
            raise _translate_sqlite(ex) from ex

    def close(self) -> None:
        try:
            self._connection.close()
        except sqlite3.Error as ex:
            raise _translate_sqlite(ex) from ex

    def columns(self, table: str) -> set[str]:
        _validate_table(table)
        try:
            rows = self._connection.execute(f"PRAGMA table_info({table})").fetchall()
        except sqlite3.Error as ex:
            raise _translate_sqlite(ex) from ex
        return {str(row[1]) for row in rows}

    def has_table(self, table: str) -> bool:
        _validate_table(table)
        try:
            row = self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
        except sqlite3.Error as ex:
            raise _translate_sqlite(ex) from ex
        return row is not None

    def __enter__(self) -> _SqliteConnection:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object | None,
    ) -> None:
        self.close()


class _PostgresRow:
    """Result row addressable by column name and position."""

    __slots__ = ("_index", "_values")

    def __init__(self, values: Sequence[Any], index: Mapping[str, int]) -> None:
        self._values = values
        self._index = index

    def __getitem__(self, key: int | str) -> Any:
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._index[key]]


class _PostgresCursor:
    """Wrap a psycopg cursor and expose rows as mapping-like objects."""

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return int(self._cursor.rowcount)

    def fetchone(self) -> Row | None:
        row = _run_postgres(self._cursor.fetchone)
        return None if row is None else _PostgresRow(tuple(row), self._index())

    def fetchall(self) -> list[Row]:
        rows = _run_postgres(self._cursor.fetchall)
        index = self._index()
        return [_PostgresRow(tuple(row), index) for row in rows]

    def _index(self) -> dict[str, int]:
        description = self._cursor.description or ()
        return {str(column.name): position for position, column in enumerate(description)}


class _PostgresConnection:
    """PostgreSQL adapter with a bounded lock wait."""

    dialect = "postgres"

    def __init__(self, connection: Any) -> None:
        self._connection = connection

    @classmethod
    def open(cls, dsn: str, *, readonly: bool = False) -> _PostgresConnection:
        """Open a psycopg connection with the service timeouts.

        Raises:
            OperationalError: psycopg is missing or the server is unreachable.
        """
        psycopg = _psycopg()
        options = f"-c lock_timeout={_POSTGRES_LOCK_TIMEOUT}"
        if readonly:
            options += " -c default_transaction_read_only=on"
        connection = _run_postgres(
            lambda: psycopg.connect(
                dsn,
                autocommit=False,
                options=options,
                connect_timeout=_POSTGRES_CONNECT_TIMEOUT,
            ),
            dsn=dsn,
        )
        return cls(connection)

    def execute(self, sql: str, params: Params = ()) -> Cursor:
        cursor = _run_postgres(self._connection.cursor)
        _run_postgres(lambda: cursor.execute(_to_postgres(sql), params or None))
        return _PostgresCursor(cursor)

    def executemany(self, sql: str, params: Rows) -> Cursor:
        cursor = _run_postgres(self._connection.cursor)
        rows = list(params)
        if rows:
            _run_postgres(lambda: cursor.executemany(_to_postgres(sql), rows))
        return _PostgresCursor(cursor)

    def begin(self) -> None:
        """Start a transaction; ``autocommit=False`` already begins one."""
        return None

    def commit(self) -> None:
        _run_postgres(self._connection.commit)

    def rollback(self) -> None:
        _run_postgres(self._connection.rollback)

    def close(self) -> None:
        _run_postgres(self._connection.close)

    def columns(self, table: str) -> set[str]:
        _validate_table(table)
        rows = self.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = ?
            """,
            (table,),
        ).fetchall()
        return {str(row["column_name"]) for row in rows}

    def has_table(self, table: str) -> bool:
        _validate_table(table)
        row = self.execute(
            """
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = current_schema() AND table_name = ?
            """,
            (table,),
        ).fetchone()
        return row is not None

    def __enter__(self) -> _PostgresConnection:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object | None,
    ) -> None:
        self.close()


def _to_postgres(sql: str) -> str:
    """Translate the service ``?`` placeholders into psycopg ``%s``."""
    return sql.replace("?", "%s")


def _ensure_sqlite_file(db_path: Path) -> None:
    """Raise the driver-style error when a read-only database is missing."""
    if db_path.is_file():
        return
    raise errors.OperationalError(f"unable to open database file: {db_path}")


def _configure(connection: sqlite3.Connection) -> None:
    """Apply the pragmas shared by writable and read-only connections."""
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA foreign_keys=ON")


__all__ = [
    "Connection",
    "Cursor",
    "Params",
    "Row",
    "Rows",
    "connect",
    "connect_readonly",
    "database_url",
    "db_exists",
    "sanitize_error_text",
]
