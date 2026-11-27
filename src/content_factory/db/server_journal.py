"""Journal writer used by one rented Vast.ai server session.

Every method opens its own connection, so the journal is safe to use from the
thread pool that races rental offers. The methods are small adapters over
:mod:`content_factory.db.generator_servers`; the recovery flow uses the same
repository functions directly. Database failures are translated into
:class:`~content_factory.exceptions.generator.JournalError`, so the generator
capsule catches one domain error and never imports the database layer.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from loguru import logger

from content_factory.db import generator_servers
from content_factory.db.connection import Connection, connect
from content_factory.db.errors import DatabaseError
from content_factory.exceptions.generator import JournalError
from content_factory.schemas.generator.server import ServerConnection


class ServerJournal:
    """Write the rental lifecycle of one generator process.

    The journal exists before the first rental and is passed into
    :class:`~content_factory.services.generator.vast.VastSession`. A failed
    row insert therefore stops the rental before ``create_instance``.
    """

    def __init__(self, db_path: Path) -> None:
        """Store the operational database path; no connection is opened yet."""
        self._db_path = db_path

    def start(
        self,
        *,
        run_id: str | None,
        profile: str | None,
        gpu: str | None,
        image: str | None,
        cost_per_hour: float | None,
    ) -> str:
        """Insert the ``renting`` row and return its journal identifier."""
        server_id = uuid4().hex
        with _write(self._db_path) as connection:
            generator_servers.insert_renting(
                connection,
                server_id=server_id,
                run_id=run_id,
                profile=profile,
                gpu=gpu,
                image=image,
                cost_per_hour=cost_per_hour,
                now=_now(),
            )
        return server_id

    def bind(self, server_id: str, contract_id: str) -> None:
        """Bind the Vast.ai contract id returned by ``create_instance``."""
        with _write(self._db_path) as connection:
            generator_servers.bind_contract(connection, server_id, contract_id, _now())

    def ready(
        self,
        server_id: str,
        connection: ServerConnection,
        cost_per_hour: float | None,
    ) -> None:
        """Store the address of the ready ComfyUI server."""
        with _write(self._db_path) as db:
            generator_servers.mark_ready(
                db,
                server_id,
                ip=connection.ip,
                port=connection.port,
                gpu=connection.gpu,
                image=connection.image,
                cost_per_hour=cost_per_hour,
                now=_now(),
            )

    def destroying(self, server_id: str, error: str | None = None) -> None:
        """Mark the rental as being destroyed now."""
        with _write(self._db_path) as connection:
            generator_servers.mark_destroying(connection, server_id, _now(), error)

    def destroyed(self, server_id: str) -> None:
        """Mark the rental destroyed after a verified teardown."""
        with _write(self._db_path) as connection:
            generator_servers.mark_destroyed(connection, server_id, _now())

    def lost(self, server_id: str, error: str | None = None) -> None:
        """Mark the terminal ``lost`` state of a vanished instance."""
        with _write(self._db_path) as connection:
            generator_servers.mark_lost(connection, server_id, _now(), error)


@contextmanager
def _write(db_path: Path) -> Iterator[Connection]:
    """Open one write connection, commit the block and translate DB failures."""
    try:
        connection = connect(db_path)
    except DatabaseError as ex:
        raise JournalError(f"журнал серверов недоступен: {ex}") from ex
    try:
        yield connection
        connection.commit()
    except DatabaseError as ex:
        _rollback(connection)
        raise JournalError(f"журнал серверов недоступен: {ex}") from ex
    except BaseException:
        _rollback(connection)
        raise
    finally:
        _close(connection)


def _rollback(connection: Connection) -> None:
    """Roll back the current transaction without masking the original error."""
    try:
        connection.rollback()
    except DatabaseError as ex:
        logger.bind(error=str(ex), error_type=type(ex).__name__).warning(
            f"Журнал серверов: откат транзакции не удался: {ex}"
        )


def _close(connection: Connection) -> None:
    """Close the connection without masking the original error."""
    try:
        connection.close()
    except DatabaseError as ex:
        logger.bind(error=str(ex), error_type=type(ex).__name__).warning(
            f"Журнал серверов: соединение не закрылось: {ex}"
        )


def _now() -> str:
    """Return the current UTC moment as ISO 8601."""
    return datetime.now(UTC).isoformat()


__all__ = ["ServerJournal"]
