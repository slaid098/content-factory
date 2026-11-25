"""Source queue and cursors of the uniqueizer.

The migration adds two tables: ``source_items`` holds metadata of every source
file seen by the importer, and ``source_cursors`` stores the last processed
path of one profile. Video bytes stay on disk; the table only points at them.
Both statements are portable between SQLite and PostgreSQL and safe to run on
a database that already has the tables.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from content_factory.schemas.database.port import Connection

VERSION = "0002"

_SOURCE_ITEMS = """
CREATE TABLE IF NOT EXISTS source_items (
    path TEXT PRIMARY KEY,
    language TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size INTEGER NOT NULL,
    duration DOUBLE PRECISION,
    added_at TEXT NOT NULL,
    used_at TEXT,
    missing_at TEXT,
    use_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE(language, profile_id, sha256)
)
"""

_SOURCE_ITEMS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_source_items_queue
ON source_items(language, profile_id, missing_at, path)
"""

_SOURCE_CURSORS = """
CREATE TABLE IF NOT EXISTS source_cursors (
    language TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    last_path TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (language, profile_id)
)
"""


def apply(connection: Connection) -> None:
    """Create the source queue tables.

    Args:
        connection: Open connection whose backend is SQLite or PostgreSQL.
    """
    connection.execute(_SOURCE_ITEMS)
    connection.execute(_SOURCE_ITEMS_INDEX)
    connection.execute(_SOURCE_CURSORS)


__all__ = ["VERSION", "apply"]
