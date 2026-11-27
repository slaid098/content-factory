"""Journal writer used by one rented Vast.ai server session.

Every method opens its own connection, so the journal is safe to use from the
thread pool that races rental offers. The methods are small adapters over
:mod:`content_factory.db.generator_servers`; the recovery flow uses the same
repository functions directly. Database failures are translated into
:class:`~content_factory.exceptions.generator.JournalError`, so the generator
capsule catches one domain error and never imports the database layer.
"""

from __future__ import annotations

from uuid import uuid4

from content_factory.db import generator_servers
from content_factory.db.journal import JournalWriter
from content_factory.schemas.generator.server import ServerConnection


class ServerJournal(JournalWriter):
    """Write the rental lifecycle of one generator process.

    The journal exists before the first rental and is passed into
    :class:`~content_factory.services.generator.vast.VastSession`. A failed
    row insert therefore stops the rental before ``create_instance``.
    """

    label = "журнал серверов"

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
        with self._write() as connection:
            generator_servers.insert_renting(
                connection,
                server_id=server_id,
                run_id=run_id,
                profile=profile,
                gpu=gpu,
                image=image,
                cost_per_hour=cost_per_hour,
                now=self._now(),
            )
        return server_id

    def bind(self, server_id: str, contract_id: str) -> None:
        """Bind the Vast.ai contract id returned by ``create_instance``."""
        with self._write() as connection:
            generator_servers.bind_contract(connection, server_id, contract_id, self._now())

    def ready(
        self,
        server_id: str,
        connection: ServerConnection,
        cost_per_hour: float | None,
    ) -> None:
        """Store the address of the ready ComfyUI server."""
        with self._write() as db:
            generator_servers.mark_ready(
                db,
                server_id,
                ip=connection.ip,
                port=connection.port,
                gpu=connection.gpu,
                image=connection.image,
                cost_per_hour=cost_per_hour,
                now=self._now(),
            )

    def destroying(self, server_id: str, error: str | None = None) -> None:
        """Mark the rental as being destroyed now."""
        with self._write() as connection:
            generator_servers.mark_destroying(connection, server_id, self._now(), error)

    def destroyed(self, server_id: str) -> None:
        """Mark the rental destroyed after a verified teardown."""
        with self._write() as connection:
            generator_servers.mark_destroyed(connection, server_id, self._now())

    def lost(self, server_id: str, error: str | None = None) -> None:
        """Mark the terminal ``lost`` state of a vanished instance."""
        with self._write() as connection:
            generator_servers.mark_lost(connection, server_id, self._now(), error)


__all__ = ["ServerJournal"]
