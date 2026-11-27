"""Shared write-connection plumbing of the generator journals.

Both the run journal and the server journal open one connection per call, so
they are safe to use from the thread pool that generates on several servers.
Only the domain label in the translated error differs between them.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from loguru import logger

from content_factory.db.connection import Connection, connect
from content_factory.db.errors import DatabaseError
from content_factory.exceptions.generator import JournalError
from content_factory.utils.clock import now_iso


class JournalWriter:
    """Open, commit and close one database connection per journal call."""

    label = "журнал"

    def __init__(self, db_path: Path) -> None:
        """Store the operational database path; no connection is opened yet."""
        self._db_path = db_path

    @contextmanager
    def _write(self) -> Iterator[Connection]:
        """Open one write connection, commit the block and translate DB failures."""
        try:
            connection = connect(self._db_path)
        except DatabaseError as ex:
            raise JournalError(f"{self.label} недоступен: {ex}") from ex
        try:
            yield connection
            connection.commit()
        except DatabaseError as ex:
            self._rollback(connection)
            raise JournalError(f"{self.label} недоступен: {ex}") from ex
        except BaseException:
            self._rollback(connection)
            raise
        finally:
            self._close(connection)

    def _rollback(self, connection: Connection) -> None:
        """Roll back the current transaction without masking the original error."""
        try:
            connection.rollback()
        except DatabaseError as ex:
            logger.bind(error=str(ex), error_type=type(ex).__name__).warning(
                f"{self.label}: откат транзакции не удался: {ex}"
            )

    def _close(self, connection: Connection) -> None:
        """Close the connection without masking the original error."""
        try:
            connection.close()
        except DatabaseError as ex:
            logger.bind(error=str(ex), error_type=type(ex).__name__).warning(
                f"{self.label}: соединение не закрылось: {ex}"
            )

    @staticmethod
    def _now() -> str:
        """Return the current UTC moment as ISO 8601."""
        return now_iso()


__all__ = ["JournalWriter"]
