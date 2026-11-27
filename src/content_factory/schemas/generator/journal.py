"""Journal ports of a generator run and of one rented server."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from content_factory.schemas.generator.server import ServerConnection


@dataclass(frozen=True, slots=True)
class JournalPlanItem:
    """One planned item written to the run journal before the first call."""

    profile: str
    file: str
    producer: str
    type: str
    look_id: str | None = None
    seed: int | None = None
    frames: int | None = None


class RunJournal(Protocol):
    """Write the progress of one generator run and of its items.

    The concrete writer lives in ``db/run_journal.py``; the pipeline, the
    producers and the legacy batch service depend only on this port, so the
    generator capsule core stays database-free.
    """

    def start_run(
        self,
        *,
        run_id: str,
        profiles: Sequence[str],
        count: int,
        content_type: str | None,
    ) -> None:
        """Insert the running row before the first network call."""
        ...

    def plan(self, run_id: str, items: Sequence[JournalPlanItem]) -> None:
        """Insert the planned items before any workflow is submitted."""
        ...

    def submitted(
        self,
        run_id: str,
        profile: str,
        file: str,
        prompt_id: str,
        attempt: int,
        contract_id: str | None,
    ) -> None:
        """Record the prompt id and attempt immediately after the submit."""
        ...

    def downloaded(
        self,
        run_id: str,
        profile: str,
        file: str,
        source: str,
    ) -> None:
        """Record the source path immediately after the download."""
        ...

    def postprocessing(
        self,
        run_id: str,
        profile: str,
        file: str,
        final: str,
    ) -> None:
        """Record the final path after the local post-processing."""
        ...

    def published(self, run_id: str, profile: str, file: str) -> None:
        """Mark the item done after its publication."""
        ...

    def failed(self, run_id: str, profile: str, file: str, error: str) -> None:
        """Mark the item failed with its reason."""
        ...

    def finish_run(self, run_id: str, status: str, error: str | None = None) -> None:
        """Close the run with its final status and reason."""
        ...

    def requeue(self, run_id: str, profile: str, file: str) -> None:
        """Drop a lost source path and put the item back into the queue."""
        ...


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


__all__ = ["JournalPlanItem", "RunJournal", "ServerJournal"]
