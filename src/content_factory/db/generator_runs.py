"""Journal rows of generator runs and of every planned item of a run.

A run row appears before the first network call, and every item row appears
before its workflow is submitted. The journal is the single source of truth
about the progress of a run: ``run.json`` stays a report and may be rebuilt
from these rows after a restart. All statements go through the shared
connection port so SQLite and PostgreSQL behave the same.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from content_factory.schemas.generator.journal import JournalPlanItem

if TYPE_CHECKING:
    from collections.abc import Sequence

    from content_factory.db.connection import Connection
    from content_factory.schemas.database.port import Row

RUNNING = "running"
DONE = "done"
FAILED = "failed"

PLANNED = "planned"
QUEUED = "queued"
DOWNLOADED = "downloaded"
POSTPROCESSING = "postprocessing"
ITEM_DONE = "done"
ITEM_FAILED = "failed"

_UNFINISHED_RUN_STATUSES = (RUNNING,)
TERMINAL_ITEM_STATUSES = (ITEM_DONE, ITEM_FAILED)
PENDING_ITEM_STATUSES = (PLANNED, QUEUED)

_RUN_COLUMNS = (
    "run_id, status, profiles, count, content_type, started_at, updated_at, finished_at, error"
)
_ITEM_COLUMNS = (
    "run_id, profile, file, producer, type, look_id, seed, frames, status, "
    "prompt_id, attempt, server_id, source_path, final_path, error, updated_at"
)

_INSERT_RUN = """
INSERT INTO generator_runs (
    run_id, status, profiles, count, content_type, started_at, updated_at,
    finished_at, error
) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)
"""

_INSERT_ITEM = """
INSERT INTO generator_items (
    run_id, profile, file, producer, type, look_id, seed, frames, status,
    prompt_id, attempt, server_id, source_path, final_path, error, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, ?)
ON CONFLICT (run_id, profile, file) DO NOTHING
"""

_FINISH_RUN = """
UPDATE generator_runs
SET status = ?, updated_at = ?, finished_at = ?, error = COALESCE(?, error)
WHERE run_id = ?
"""

_TOUCH_RUN = """
UPDATE generator_runs SET updated_at = ? WHERE run_id = ?
"""

_SUBMITTED = """
UPDATE generator_items
SET status = ?, prompt_id = ?, attempt = ?, server_id = ?, error = NULL,
    updated_at = ?
WHERE run_id = ? AND profile = ? AND file = ?
"""

_DOWNLOADED = """
UPDATE generator_items
SET status = ?, source_path = ?, error = NULL, updated_at = ?
WHERE run_id = ? AND profile = ? AND file = ?
"""

_POSTPROCESSING = """
UPDATE generator_items
SET status = ?, final_path = ?, error = NULL, updated_at = ?
WHERE run_id = ? AND profile = ? AND file = ?
"""

_PUBLISHED = """
UPDATE generator_items
SET status = ?, error = NULL, updated_at = ?
WHERE run_id = ? AND profile = ? AND file = ?
"""

_FAILED = """
UPDATE generator_items
SET status = ?, error = ?, updated_at = ?
WHERE run_id = ? AND profile = ? AND file = ?
"""

_REQUEUE = """
UPDATE generator_items
SET status = ?, source_path = NULL, error = NULL, updated_at = ?
WHERE run_id = ? AND profile = ? AND file = ?
"""


@dataclass(frozen=True, slots=True)
class GeneratorRunRow:
    """One ``generator_runs`` row exactly as stored."""

    run_id: str
    status: str
    profiles: tuple[str, ...]
    count: int
    content_type: str | None
    started_at: str
    updated_at: str
    finished_at: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class GeneratorItemRow:
    """One ``generator_items`` row exactly as stored."""

    run_id: str
    profile: str
    file: str
    producer: str
    type: str
    look_id: str | None
    seed: int | None
    frames: int | None
    status: str
    prompt_id: str | None
    attempt: int | None
    server_id: str | None
    source_path: str | None
    final_path: str | None
    error: str | None
    updated_at: str


def insert_run(
    connection: Connection,
    *,
    run_id: str,
    profiles: Sequence[str],
    count: int,
    content_type: str | None,
    now: str,
) -> None:
    """Insert the ``running`` row of a run before any network call."""
    connection.execute(
        _INSERT_RUN,
        (
            run_id,
            RUNNING,
            json.dumps(list(profiles), ensure_ascii=False),
            count,
            content_type,
            now,
            now,
        ),
    )


def insert_plan(
    connection: Connection,
    run_id: str,
    items: Sequence[JournalPlanItem],
    now: str,
) -> None:
    """Insert planned item rows, ignoring items already journaled."""
    connection.executemany(
        _INSERT_ITEM,
        [
            (
                run_id,
                item.profile,
                item.file,
                item.producer,
                item.type,
                item.look_id,
                item.seed,
                item.frames,
                PLANNED,
                now,
            )
            for item in items
        ],
    )
    connection.execute(_TOUCH_RUN, (now, run_id))


def mark_submitted(
    connection: Connection,
    *,
    run_id: str,
    profile: str,
    file: str,
    prompt_id: str,
    attempt: int,
    server_id: str | None,
    now: str,
) -> None:
    """Record the prompt id and attempt immediately after the submit."""
    connection.execute(
        _SUBMITTED,
        (QUEUED, prompt_id, attempt, server_id, now, run_id, profile, file),
    )


def mark_downloaded(
    connection: Connection,
    *,
    run_id: str,
    profile: str,
    file: str,
    source_path: str,
    now: str,
) -> None:
    """Record the source path immediately after the download."""
    connection.execute(
        _DOWNLOADED,
        (DOWNLOADED, source_path, now, run_id, profile, file),
    )


def mark_postprocessing(
    connection: Connection,
    *,
    run_id: str,
    profile: str,
    file: str,
    final_path: str,
    now: str,
) -> None:
    """Record the final path after the local post-processing."""
    connection.execute(
        _POSTPROCESSING,
        (POSTPROCESSING, final_path, now, run_id, profile, file),
    )


def mark_published(
    connection: Connection,
    *,
    run_id: str,
    profile: str,
    file: str,
    now: str,
) -> None:
    """Mark the item done after its publication into the stock."""
    connection.execute(_PUBLISHED, (ITEM_DONE, now, run_id, profile, file))


def mark_failed(
    connection: Connection,
    *,
    run_id: str,
    profile: str,
    file: str,
    error: str,
    now: str,
) -> None:
    """Mark the item failed with its reason."""
    connection.execute(_FAILED, (ITEM_FAILED, error, now, run_id, profile, file))


def requeue(
    connection: Connection,
    *,
    run_id: str,
    profile: str,
    file: str,
    now: str,
) -> None:
    """Drop a lost source path and put the item back into the remote queue."""
    connection.execute(_REQUEUE, (QUEUED, now, run_id, profile, file))


def finish_run(
    connection: Connection,
    run_id: str,
    status: str,
    *,
    now: str,
    error: str | None = None,
) -> None:
    """Close a run with its final status and reason."""
    connection.execute(_FINISH_RUN, (status, now, now, error, run_id))


def get_run(connection: Connection, run_id: str) -> GeneratorRunRow | None:
    """Return one run row by id, or ``None``."""
    row = connection.execute(
        f"SELECT {_RUN_COLUMNS} FROM generator_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return None if row is None else _run_row(row)


def list_unfinished_runs(connection: Connection) -> list[GeneratorRunRow]:
    """Return every run that has not reached a terminal state yet."""
    placeholders = ", ".join("?" for _ in _UNFINISHED_RUN_STATUSES)
    rows = connection.execute(
        f"""
        SELECT {_RUN_COLUMNS} FROM generator_runs
        WHERE status IN ({placeholders})
        ORDER BY started_at, run_id
        """,
        _UNFINISHED_RUN_STATUSES,
    ).fetchall()
    return [_run_row(row) for row in rows]


def list_items(connection: Connection, run_id: str) -> list[GeneratorItemRow]:
    """Return every item of *run_id* in its planned order."""
    rows = connection.execute(
        f"""
        SELECT {_ITEM_COLUMNS} FROM generator_items
        WHERE run_id = ?
        ORDER BY profile, file
        """,
        (run_id,),
    ).fetchall()
    return [_item_row(row) for row in rows]


def _run_row(row: Row) -> GeneratorRunRow:
    """Build one run row from a database row."""
    raw = json.loads(str(row["profiles"]))
    profiles = tuple(str(name) for name in raw) if isinstance(raw, list) else ()
    return GeneratorRunRow(
        run_id=str(row["run_id"]),
        status=str(row["status"]),
        profiles=profiles,
        count=int(row["count"]),
        content_type=None if row["content_type"] is None else str(row["content_type"]),
        started_at=str(row["started_at"]),
        updated_at=str(row["updated_at"]),
        finished_at=None if row["finished_at"] is None else str(row["finished_at"]),
        error=None if row["error"] is None else str(row["error"]),
    )


def _item_row(row: Row) -> GeneratorItemRow:
    """Build one item row from a database row."""
    return GeneratorItemRow(
        run_id=str(row["run_id"]),
        profile=str(row["profile"]),
        file=str(row["file"]),
        producer=str(row["producer"]),
        type=str(row["type"]),
        look_id=None if row["look_id"] is None else str(row["look_id"]),
        seed=None if row["seed"] is None else int(row["seed"]),
        frames=None if row["frames"] is None else int(row["frames"]),
        status=str(row["status"]),
        prompt_id=None if row["prompt_id"] is None else str(row["prompt_id"]),
        attempt=None if row["attempt"] is None else int(row["attempt"]),
        server_id=None if row["server_id"] is None else str(row["server_id"]),
        source_path=None if row["source_path"] is None else str(row["source_path"]),
        final_path=None if row["final_path"] is None else str(row["final_path"]),
        error=None if row["error"] is None else str(row["error"]),
        updated_at=str(row["updated_at"]),
    )


__all__ = [
    "DONE",
    "DOWNLOADED",
    "FAILED",
    "ITEM_DONE",
    "ITEM_FAILED",
    "PENDING_ITEM_STATUSES",
    "PLANNED",
    "POSTPROCESSING",
    "QUEUED",
    "RUNNING",
    "TERMINAL_ITEM_STATUSES",
    "GeneratorItemRow",
    "GeneratorRunRow",
    "finish_run",
    "get_run",
    "insert_plan",
    "insert_run",
    "list_items",
    "list_unfinished_runs",
    "mark_downloaded",
    "mark_failed",
    "mark_postprocessing",
    "mark_published",
    "mark_submitted",
    "requeue",
]
