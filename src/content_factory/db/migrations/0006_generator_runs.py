"""Journal of generator runs and of every planned item of a run.

A run appears before the first network call and its items are written before
any workflow is submitted. The journal is the source of truth about the
progress of a run: ``run.json`` stays a report. Item rows reference the
rented server of their chunk, so recovery can connect to the same instance
instead of renting another one. The statements are portable between SQLite
and PostgreSQL and safe to apply twice.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from content_factory.schemas.database.port import Connection

VERSION = "0006"

_GENERATOR_RUNS = """
CREATE TABLE IF NOT EXISTS generator_runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    profiles TEXT NOT NULL,
    count INTEGER NOT NULL,
    content_type TEXT,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    error TEXT
)
"""

_GENERATOR_ITEMS = """
CREATE TABLE IF NOT EXISTS generator_items (
    run_id TEXT NOT NULL,
    profile TEXT NOT NULL,
    file TEXT NOT NULL,
    producer TEXT NOT NULL,
    type TEXT NOT NULL,
    look_id TEXT,
    seed INTEGER,
    frames INTEGER,
    status TEXT NOT NULL,
    prompt_id TEXT,
    attempt INTEGER,
    server_id TEXT,
    source_path TEXT,
    final_path TEXT,
    error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (run_id, profile, file),
    FOREIGN KEY (server_id) REFERENCES generator_servers (id)
)
"""

_ITEMS_INDEX = """
CREATE INDEX IF NOT EXISTS generator_items_run_status
ON generator_items (run_id, status)
"""


def apply(connection: Connection) -> None:
    """Create the run and item journal tables with their lookup index.

    Args:
        connection: Open connection whose backend is SQLite or PostgreSQL.
    """
    connection.execute(_GENERATOR_RUNS)
    connection.execute(_GENERATOR_ITEMS)
    connection.execute(_ITEMS_INDEX)


__all__ = ["VERSION", "apply"]
