"""Journal writer used by one generator run.

Every method opens its own connection and commits it, so the writer is safe
to call from the thread pool that generates chunks on several servers. The
methods are small adapters over :mod:`content_factory.db.generator_runs`; the
recovery flow uses the same repository functions directly. Database failures
are translated into :class:`~content_factory.exceptions.generator.JournalError`,
so the generator capsule catches one domain error and never imports the
database layer.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from content_factory.db import generator_runs, generator_servers
from content_factory.db.journal import JournalWriter
from content_factory.schemas.generator.journal import JournalPlanItem

if TYPE_CHECKING:
    from content_factory.db.connection import Connection


class RunJournalWriter(JournalWriter):
    """Write the progress of one generator run into the operational database."""

    label = "журнал ранов"

    def start_run(
        self,
        *,
        run_id: str,
        profiles: Sequence[str],
        count: int,
        content_type: str | None,
    ) -> None:
        """Insert the running row before the first network call."""
        with self._write() as connection:
            generator_runs.insert_run(
                connection,
                run_id=run_id,
                profiles=profiles,
                count=count,
                content_type=content_type,
                now=self._now(),
            )

    def plan(self, run_id: str, items: Sequence[JournalPlanItem]) -> None:
        """Insert the planned items before any workflow is submitted."""
        with self._write() as connection:
            generator_runs.insert_plan(connection, run_id, items, self._now())

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
        with self._write() as connection:
            server_id = _server_id(connection, contract_id)
            generator_runs.mark_submitted(
                connection,
                run_id=run_id,
                profile=profile,
                file=file,
                prompt_id=prompt_id,
                attempt=attempt,
                server_id=server_id,
                now=self._now(),
            )

    def downloaded(
        self,
        run_id: str,
        profile: str,
        file: str,
        source: str,
    ) -> None:
        """Record the source path immediately after the download."""
        with self._write() as connection:
            generator_runs.mark_downloaded(
                connection,
                run_id=run_id,
                profile=profile,
                file=file,
                source_path=source,
                now=self._now(),
            )

    def postprocessing(
        self,
        run_id: str,
        profile: str,
        file: str,
        final: str,
    ) -> None:
        """Record the final path after the local post-processing."""
        with self._write() as connection:
            generator_runs.mark_postprocessing(
                connection,
                run_id=run_id,
                profile=profile,
                file=file,
                final_path=final,
                now=self._now(),
            )

    def published(self, run_id: str, profile: str, file: str) -> None:
        """Mark the item done after its publication."""
        with self._write() as connection:
            generator_runs.mark_published(
                connection,
                run_id=run_id,
                profile=profile,
                file=file,
                now=self._now(),
            )

    def failed(self, run_id: str, profile: str, file: str, error: str) -> None:
        """Mark the item failed with its reason."""
        with self._write() as connection:
            generator_runs.mark_failed(
                connection,
                run_id=run_id,
                profile=profile,
                file=file,
                error=error,
                now=self._now(),
            )

    def finish_run(self, run_id: str, status: str, error: str | None = None) -> None:
        """Close the run with its final status and reason."""
        with self._write() as connection:
            generator_runs.finish_run(
                connection,
                run_id,
                status,
                now=self._now(),
                error=error,
            )

    def requeue(self, run_id: str, profile: str, file: str) -> None:
        """Drop a lost source path and put the item back into the queue."""
        with self._write() as connection:
            generator_runs.requeue(
                connection,
                run_id=run_id,
                profile=profile,
                file=file,
                now=self._now(),
            )


def _server_id(connection: Connection, contract_id: str | None) -> str | None:
    """Return the journal id of a ready server, or ``None`` when unknown."""
    if not contract_id:
        return None
    row = generator_servers.get_by_contract(connection, contract_id)
    return None if row is None else row.id


__all__ = ["RunJournalWriter"]
