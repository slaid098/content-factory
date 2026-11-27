"""Journal port of one rented Vast.ai generator server."""

from __future__ import annotations

from typing import Protocol

from content_factory.schemas.generator.server import ServerConnection


class ServerJournal(Protocol):
    """Write the rental lifecycle of one generator process.

    The concrete writer lives in ``db/server_journal.py``; the rental session
    depends only on this port, so the capsule core stays database-free.
    """

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
        ...

    def bind(self, server_id: str, contract_id: str) -> None:
        """Bind the Vast.ai contract id returned by ``create_instance``."""
        ...

    def ready(
        self,
        server_id: str,
        connection: ServerConnection,
        cost_per_hour: float | None,
    ) -> None:
        """Store the address of the ready ComfyUI server."""
        ...

    def destroying(self, server_id: str, error: str | None = None) -> None:
        """Mark the rental as being destroyed now."""
        ...

    def destroyed(self, server_id: str) -> None:
        """Mark the rental destroyed after a verified teardown."""
        ...

    def lost(self, server_id: str, error: str | None = None) -> None:
        """Mark the terminal ``lost`` state of a vanished instance."""
        ...


__all__ = ["ServerJournal"]
