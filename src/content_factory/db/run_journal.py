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

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from content_factory.db import generator_runs, generator_servers
from content_factory.db.connection import Connection, connect
from content_factory.db.errors import DatabaseError
from content_factory.exceptions.generator import JournalError
from content_factory.schemas.generator.journal import JournalPlanItem


class RunJournalWriter:
    """Write the progress of one generator run into the operational database."""

    def __init__(self, db_path: Path) -> None:
        """Store the operational database path; no connection is opened yet."""
        self._db_path = db_path

    def start_run(
        self,
        *,
        run_id: str,
        profiles: Sequence[str],
        count: int,
        content_type: str | None,
    ) -> None:
        """Insert the running row before the first network call."""
        with _write(self._db_path) as connection:
            generator_runs.insert_run(
                connection,
                run_id=run_id,
                profiles=profiles,
                count=count,
                content_type=content_type,
                now=_now(),
            )

    def plan(self, run_id: str, items: Sequence[JournalPlanItem]) -> None:
        """Insert the planned items before any workflow is submitted."""
        with _write(self._db_path) as connection:
            generator_runs.insert_plan(connection, run_id, items, _now())

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
        with _write(self._db_path) as connection:
            server_id = _server_id(connection, contract_id)
            generator_runs.mark_submitted(
                connection,
                run_id=run_id,
                profile=profile,
                file=file,
                prompt_id=prompt_id,
                attempt=attempt,
                server_id=server_id,
                now=_now(),
            )

    def downloaded(
        self,
        run_id: str,
        profile: str,
        file: str,
        source: str,
    ) -> None:
        """Record the source path immediately after the download."""
        with _write(self._db_path) as connection:
            generator_runs.mark_downloaded(
                connection,
                run_id=run_id,
                profile=profile,
                file=file,
                source_path=source,
                now=_now(),
            )

    def postprocessing(
        self,
        run_id: str,
        profile: str,
        file: str,
        final: str,
    ) -> None:
        """Record the final path after the local post-processing."""
        with _write(self._db_path) as connection:
            generator_runs.mark_postprocessing(
                connection,
                run_id=run_id,
                profile=profile,
                file=file,
                final_path=final,
                now=_now(),
            )

    def published(self, run_id: str, profile: str, file: str) -> None:
        """Mark the item done after its publication."""
        with _write(self._db_path) as connection:
            generator_runs.mark_published(
                connection,
                run_id=run_id,
                profile=profile,
                file=file,
                now=_now(),
            )

    def failed(self, run_id: str, profile: str, file: str, error: str) -> None:
        """Mark the item failed with its reason."""
        with _write(self._db_path) as connection:
            generator_runs.mark_failed(
                connection,
                run_id=run_id,
                profile=profile,
                file=file,
                error=error,
                now=_now(),
            )

    def finish_run(self, run_id: str, status: str, error: str | None = None) -> None:
        """Close the run with its final status and reason."""
        with _write(self._db_path) as connection:
            generator_runs.finish_run(
                connection,
                run_id,
                status,
                now=_now(),
                error=error,
            )

    def requeue(self, run_id: str, profile: str, file: str) -> None:
        """Drop a lost source path and put the item back into the queue."""
        with _write(self._db_path) as connection:
            generator_runs.requeue(
                connection,
                run_id=run_id,
                profile=profile,
                file=file,
                now=_now(),
            )


def _server_id(connection: Connection, contract_id: str | None) -> str | None:
    """Return the journal id of a ready server, or ``None`` when unknown."""
    if not contract_id:
        return None
    row = generator_servers.get_by_contract(connection, contract_id)
    return None if row is None else row.id


@contextmanager
def _write(db_path: Path) -> Iterator[Connection]:
    """Open one write connection, commit the block and translate DB failures."""
    try:
        connection = connect(db_path)
    except DatabaseError as ex:
        raise JournalError(f"журнал ранов недоступен: {ex}") from ex
    try:
        yield connection
        connection.commit()
    except DatabaseError as ex:
        _rollback(connection)
        raise JournalError(f"журнал ранов недоступен: {ex}") from ex
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
            f"Журнал ранов: откат транзакции не удался: {ex}"
        )


def _close(connection: Connection) -> None:
    """Close the connection without masking the original error."""
    try:
        connection.close()
    except DatabaseError as ex:
        logger.bind(error=str(ex), error_type=type(ex).__name__).warning(
            f"Журнал ранов: соединение не закрылось: {ex}"
        )


def _now() -> str:
    """Return the current UTC moment as ISO 8601."""
    return datetime.now(UTC).isoformat()


__all__ = ["RunJournalWriter"]
