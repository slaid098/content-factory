"""SQLite index of stock files and their delivery history."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence

from content_factory.schemas.content.delivery import ContentType
from content_factory.schemas.stock.layout import StockFile

_SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_items (
    path TEXT PRIMARY KEY,
    language TEXT NOT NULL,
    niche TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'video',
    profile_id TEXT,
    size INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    delivered_at TEXT,
    request_id TEXT
)
"""

_ADD_CONTENT_TYPE = "ALTER TABLE stock_items ADD COLUMN content_type TEXT NOT NULL DEFAULT 'video'"

_LEGACY_LANGUAGE_INDEX_DROP = "DROP INDEX IF EXISTS idx_stock_items_language_delivered"

_LANGUAGE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_stock_items_language_type_delivered
ON stock_items(language, content_type, delivered_at)
"""

_PROFILE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_stock_items_profile_delivered
ON stock_items(profile_id, delivered_at)
"""

_UPSERT = """
INSERT INTO stock_items (
    path, language, niche, content_type, profile_id, size, created_at, delivered_at, request_id
) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)
ON CONFLICT(path) DO UPDATE SET
    language = excluded.language,
    niche = excluded.niche,
    content_type = excluded.content_type,
    size = excluded.size,
    profile_id = COALESCE(excluded.profile_id, stock_items.profile_id)
"""

_BATCH_SIZE = 500


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the stock table, migrate its columns and create lookup indexes.

    A database created before content types existed gets the ``content_type``
    column with every existing row marked as ``video``, so video delivery
    keeps working without a reindex.

    Args:
        connection: Open connection to the operational database.
    """
    connection.execute(_SCHEMA)
    _ensure_content_type_column(connection)
    connection.execute(_LEGACY_LANGUAGE_INDEX_DROP)
    connection.execute(_LANGUAGE_INDEX)
    connection.execute(_PROFILE_INDEX)
    connection.commit()


def _ensure_content_type_column(connection: sqlite3.Connection) -> None:
    """Add the content type column to a database that does not have it."""
    columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(stock_items)")}
    if "content_type" not in columns:
        connection.execute(_ADD_CONTENT_TYPE)


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
    *,
    content_type: ContentType = ContentType.VIDEO,
    profile_id: str | None = None,
    niche: str | None = None,
    unassigned_only: bool = False,
) -> list[tuple[str, str | None]]:
    """Return undelivered stock paths of one language and type in path order.

    The filters combine with AND; the type defaults to video, and the other
    filters are off by default, so a call with only ``language`` returns every
    available video row of that language.

    Args:
        connection: Open connection to the operational database.
        language: Validated two- or three-letter language code.
        content_type: Restrict the rows to this media type.
        profile_id: Restrict the rows to this profile id.
        niche: Restrict the rows to this niche such as ``general``.
        unassigned_only: Return only rows without a profile id.

    Returns:
        ``(relative path, profile id)`` pairs; the profile id is ``None`` when
        the file name does not identify one.
    """
    conditions = ["language = ?", "content_type = ?", "delivered_at IS NULL"]
    params: list[str] = [language, content_type.value]
    if profile_id is not None:
        conditions.append("profile_id = ?")
        params.append(profile_id)
    if niche is not None:
        conditions.append("niche = ?")
        params.append(niche)
    if unassigned_only:
        conditions.append("profile_id IS NULL")
    rows = connection.execute(
        f"""
        SELECT path, profile_id FROM stock_items
        WHERE {" AND ".join(conditions)}
        ORDER BY path
        """,
        params,
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


def count_by_profile(
    connection: sqlite3.Connection,
    profile_id: str,
    *,
    content_type: ContentType = ContentType.VIDEO,
) -> int:
    """Count the undelivered stock files of one profile and type.

    Args:
        connection: Open connection to the operational database.
        profile_id: Profile identifier such as ``dating/profile_1``.
        content_type: Media type to count; video by default, so photos do not
            change the profile refill decision.

    Returns:
        Number of available files attributed to the profile.
    """
    row = connection.execute(
        """
        SELECT COUNT(*) FROM stock_items
        WHERE profile_id = ? AND content_type = ? AND delivered_at IS NULL
        """,
        (profile_id, content_type.value),
    ).fetchone()
    return 0 if row is None else int(row[0])


def _params(
    item: StockFile, size: int, created_at: str
) -> tuple[str, str, str, str, str | None, int, str]:
    """Build the bound parameters of one upsert."""
    return (
        item.path.as_posix(),
        item.language,
        item.niche,
        item.content_type.value,
        item.profile_id,
        size,
        created_at,
    )
