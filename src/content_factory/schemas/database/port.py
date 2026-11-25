"""Protocols of the database connection port.

The service talks to SQLite and PostgreSQL through the same small surface, so
the protocols live in ``schemas``: callers outside ``db/`` annotate with them
without importing the driver layer at runtime.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Protocol

Params = Sequence[Any] | Mapping[str, Any]
Rows = Iterable[Sequence[Any] | Mapping[str, Any]]


class Row(Protocol):
    """A result row addressable both by column name and by position."""

    def __getitem__(self, key: int | str) -> Any:
        """Return one column value."""


class Cursor(Protocol):
    """The part of a driver cursor the service uses."""

    @property
    def rowcount(self) -> int:
        """Return the number of affected rows of the last statement."""

    def fetchone(self) -> Row | None:
        """Return the first row or ``None``."""

    def fetchall(self) -> list[Row]:
        """Return every remaining row."""


class Connection(Protocol):
    """A database connection independent of the driver behind it."""

    @property
    def dialect(self) -> str:
        """Return ``sqlite`` or ``postgres``."""

    def execute(self, sql: str, params: Params = ()) -> Cursor:
        """Run one statement with ``?`` placeholders and return its cursor."""

    def executemany(self, sql: str, params: Rows) -> Cursor:
        """Run one statement for many parameter sets."""

    def begin(self) -> None:
        """Start a transaction that covers DDL on SQLite; no-op on PostgreSQL."""

    def commit(self) -> None:
        """Commit the current transaction."""

    def rollback(self) -> None:
        """Roll back the current transaction."""

    def close(self) -> None:
        """Close the connection."""

    def columns(self, table: str) -> set[str]:
        """Return the column names of one table."""

    def has_table(self, table: str) -> bool:
        """Return whether the table exists in the current schema."""

    def __enter__(self) -> Connection:
        """Enter the connection context."""

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: object | None,
    ) -> None:
        """Close the connection on context exit."""


__all__ = ["Connection", "Cursor", "Params", "Row", "Rows"]
