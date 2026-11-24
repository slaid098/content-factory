"""SQLite index of stock files and their delivery history."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence

from content_factory.schemas.stock.layout import StockFile

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_items (
    path TEXT PRIMARY KEY,
    language TEXT NOT NULL,
    niche TEXT NOT NULL,
    profile_id TEXT,
    size INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    request_id TEXT
)
"""

_LANGUAGE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_stock_items_language_delivered
ON stock_items(language, delivered_at)
"""

_PROFILE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_stock_items_profile_delivered
ON stock_items(profile_id, delivered_at)
"""

_UPSERT = """
INSERT INTO stock_items (
    path, language, niche, profile_id, size, created_at, delivered_at, request_id
) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)
ON CONFLICT(path) DO UPDATE SET
    language = excluded.language,
    niche = excluded.niche,
    size = excluded.size,
    profile_id = COALESCE(excluded.profile_id, stock_items.profile_id)
"""

_BATCH_SIZE = 500


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the stock table and its lookup indexes when missing.

    Args:
        connection: Open connection to the operational database.
    """
    connection.execute(_SCHEMA)
    connection.execute(_LANGUAGE_INDEX)
    connection.execute(_PROFILE_INDEX)
    connection.commit()


def upsert_item(
    connection: sqlite3.Connection,
    item: StockFile,
    size: int,
    created_at: str,
) -> None:
    """Insert one stock file or refresh its mutable disk-backed fields.

    The delivery history is never overwritten: ``delivered_at`` and
    ``request_id`` keep their values, and an unknown profile id of a new scan
    does not erase a known one.

    Args:
        connection: Open connection to the operational database.
        item: Stock file whose ``path`` is the stored relative POSIX path.
        size: File size in bytes.
        created_at: ISO 8601 creation moment of the stored row.
    """
    connection.execute(_UPSERT, _params(item, size, created_at))


def upsert_items(
    connection: sqlite3.Connection,
    items: Iterable[tuple[StockFile, int, str]],
) -> None:
    """Insert or refresh many stock files in one batch.

    Args:
        connection: Open connection to the operational database.
        items: ``(file, size, created_at)`` rows of one synchronization pass.
    """
    connection.executemany(_UPSERT, [_params(*row) for row in items])


def prune_missing(connection: sqlite3.Connection, existing: set[str]) -> int:
    """Delete undelivered rows whose files are absent from the last scan.

    Delivered rows are history and stay even when their file is gone.

    Args:
        connection: Open connection to the operational database.
        existing: Relative POSIX paths found on disk.

    Returns:
        Number of deleted rows.
    """
    rows = connection.execute("SELECT path FROM stock_items WHERE delivered_at IS NULL").fetchall()
    missing = [str(row[0]) for row in rows if str(row[0]) not in existing]
    removed = 0
    for start in range(0, len(missing), _BATCH_SIZE):
        batch = missing[start : start + _BATCH_SIZE]
        placeholders = ", ".join("?" for _ in batch)
        cursor = connection.execute(
            f"DELETE FROM stock_items WHERE delivered_at IS NULL AND path IN ({placeholders})",
            batch,
        )
        removed += cursor.rowcount
    return removed


def list_available(
    connection: sqlite3.Connection,
    language: str,
) -> list[tuple[str, str | None]]:
    """Return undelivered stock paths of one language in path order.

    Args:
        connection: Open connection to the operational database.
        language: Validated two- or three-letter language code.

    Returns:
        ``(relative path, profile id)`` pairs; the profile id is ``None`` when
        the file name does not identify one.
    """
    rows = connection.execute(
        """
        SELECT path, profile_id FROM stock_items
        WHERE language = ? AND delivered_at IS NULL
        ORDER BY path
        """,
        (language,),
    ).fetchall()
    return [(str(row[0]), None if row[1] is None else str(row[1])) for row in rows]


def mark_delivered(
    connection: sqlite3.Connection,
    paths: Sequence[str],
    request_id: str,
    now: str,
) -> None:
    """Record the delivery moment and request of the selected stock rows.

    Args:
        connection: Open connection to the operational database.
        paths: Relative POSIX paths of the delivered files.
        request_id: Client request identifier that received the archive.
        now: ISO 8601 delivery moment.
    """
    connection.executemany(
        """
        UPDATE stock_items
        SET delivered_at = ?, request_id = ?
        WHERE path = ?
        """,
        [(now, request_id, path) for path in paths],
    )


def count_by_profile(connection: sqlite3.Connection, profile_id: str) -> int:
    """Count the undelivered stock files of one profile.

    Args:
        connection: Open connection to the operational database.
        profile_id: Profile identifier such as ``dating/profile_1``.

    Returns:
        Number of available files attributed to the profile.
    """
    row = connection.execute(
        """
        SELECT COUNT(*) FROM stock_items
        WHERE profile_id = ? AND delivered_at IS NULL
        """,
        (profile_id,),
    ).fetchone()
    return 0 if row is None else int(row[0])


def _params(
    item: StockFile, size: int, created_at: str
) -> tuple[str, str, str, str | None, int, str]:
    """Build the bound parameters of one upsert."""
    return (item.path.as_posix(), item.language, item.niche, item.profile_id, size, created_at)
