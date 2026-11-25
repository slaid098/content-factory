"""SQL repository of the profile source pool and its circular cursor.

Every statement that touches ``source_items`` or ``source_cursors`` lives
here. Callers pass an open connection of the shared port, so the repository
works with SQLite in development and PostgreSQL in production. The functions
do not commit: one batch marks a source used and moves the cursor in the same
transaction, and the caller decides when that transaction ends.
"""

from __future__ import annotations

from dataclasses import replace

from content_factory.db.connection import Connection
from content_factory.db.errors import IntegrityError
from content_factory.schemas.database.port import Row
from content_factory.schemas.generator.source import SourceImportAction, SourceItem

_COLUMNS = """
    id, language, profile_id, path, sha256, size, duration,
    added_at, used_at, use_count, missing_at
"""

_POOL_FILTER = "language = ? AND profile_id = ? AND missing_at IS NULL"


def upsert_import(
    connection: Connection,
    *,
    language: str,
    profile_id: str,
    path: str,
    sha256: str,
    size: int,
    duration: float,
    now: str,
) -> SourceImportAction:
    """Insert or refresh one imported source file.

    A file with the same relative path and hash only refreshes its size and
    duration and clears ``missing_at``. A new hash at the same path is a new
    source: its usage history is reset. The same hash of another file of the
    same profile is a duplicate and changes nothing.

    Args:
        connection: Open connection to the operational database.
        language: Language folder of the source.
        profile_id: Profile identifier such as ``dating/profile_1``.
        path: Relative POSIX path from the data directory.
        sha256: Hex digest of the file contents.
        size: File size in bytes.
        duration: Source duration in seconds from ffprobe.
        now: ISO 8601 moment of the import.

    Returns:
        What the upsert did with the file.
    """
    current = connection.execute(
        "SELECT id, sha256, missing_at FROM source_items WHERE path = ?",
        (path,),
    ).fetchone()
    if current is not None:
        current_id = int(current["id"])
        if str(current["sha256"]) == sha256:
            was_missing = current["missing_at"] is not None
            connection.execute(
                "UPDATE source_items SET size = ?, duration = ?, missing_at = NULL WHERE id = ?",
                (size, duration, current_id),
            )
            return SourceImportAction.RESTORED if was_missing else SourceImportAction.UNCHANGED
        if _hash_taken(connection, language, profile_id, sha256, exclude=current_id):
            return SourceImportAction.DUPLICATE
        connection.execute(
            """
            UPDATE source_items
            SET language = ?, profile_id = ?, sha256 = ?, size = ?, duration = ?,
                added_at = ?, used_at = NULL, use_count = 0, missing_at = NULL
            WHERE id = ?
            """,
            (language, profile_id, sha256, size, duration, now, current_id),
        )
        return SourceImportAction.UPDATED
    if _hash_taken(connection, language, profile_id, sha256):
        return SourceImportAction.DUPLICATE
    connection.execute(
        """
        INSERT INTO source_items (
            language, profile_id, path, sha256, size, duration,
            added_at, used_at, use_count, missing_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 0, NULL)
        """,
        (language, profile_id, path, sha256, size, duration, now),
    )
    return SourceImportAction.ADDED


def select_next(
    connection: Connection,
    language: str,
    profile_id: str,
    count: int,
) -> tuple[SourceItem, ...]:
    """Select the next available sources after the cursor, wrapping once.

    Rows marked ``missing_at`` are skipped. The first query takes rows with a
    greater id, the second continues from the beginning, so one source never
    repeats inside a single selection.

    Args:
        connection: Open connection to the operational database.
        language: Language of the profile.
        profile_id: Profile identifier.
        count: Maximum number of sources to return.

    Returns:
        Up to *count* sources in cursor order.
    """
    if count <= 0:
        return ()
    cursor = connection.execute(
        "SELECT last_source_id FROM source_cursors WHERE language = ? AND profile_id = ?",
        (language, profile_id),
    ).fetchone()
    last = (
        0 if cursor is None or cursor["last_source_id"] is None else int(cursor["last_source_id"])
    )
    params = (language, profile_id, last)
    rows = list(
        connection.execute(
            f"""
            SELECT {_COLUMNS} FROM source_items
            WHERE {_POOL_FILTER} AND id > ?
            ORDER BY id
            LIMIT ?
            """,
            (*params, count),
        ).fetchall()
    )
    if len(rows) < count:
        rows.extend(
            connection.execute(
                f"""
                SELECT {_COLUMNS} FROM source_items
                WHERE {_POOL_FILTER} AND id <= ?
                ORDER BY id
                LIMIT ?
                """,
                (*params, count - len(rows)),
            ).fetchall()
        )
    return tuple(_item(row) for row in rows)


def mark_used(connection: Connection, source_id: int, now: str) -> None:
    """Record a successful use of one source and move the profile cursor.

    Both writes belong to the caller's transaction, so the cursor never gets
    ahead of the usage mark. The cursor moves to the last successfully
    published source; a failed item does not call this function.

    Args:
        connection: Open connection to the operational database.
        source_id: Primary key of the used source.
        now: ISO 8601 moment of the publication.

    Raises:
        IntegrityError: The source row does not exist.
    """
    row = connection.execute(
        "SELECT language, profile_id FROM source_items WHERE id = ?",
        (source_id,),
    ).fetchone()
    if row is None:
        raise IntegrityError(f"исходник не найден: id={source_id}")
    language, profile_id = str(row["language"]), str(row["profile_id"])
    connection.execute(
        """
        UPDATE source_items
        SET used_at = ?, use_count = use_count + 1
        WHERE id = ?
        """,
        (now, source_id),
    )
    connection.execute(
        """
        INSERT INTO source_cursors (language, profile_id, last_source_id, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(language, profile_id) DO UPDATE SET
            last_source_id = excluded.last_source_id,
            updated_at = excluded.updated_at
        """,
        (language, profile_id, source_id, now),
    )


def mark_missing(
    connection: Connection,
    present: set[str],
    now: str,
    *,
    root: str = "",
) -> tuple[SourceItem, ...]:
    """Mark imported rows whose files were not found by the latest scan.

    Args:
        connection: Open connection to the operational database.
        present: Relative POSIX paths found on disk.
        now: ISO 8601 moment of the import.
        root: Relative POSIX directory that was scanned; rows outside it
            belong to another import root and are left untouched. An empty
            string covers the whole pool.

    Returns:
        Rows that got ``missing_at`` in this call.
    """
    rows = connection.execute(
        f"SELECT {_COLUMNS} FROM source_items WHERE missing_at IS NULL ORDER BY id"
    ).fetchall()
    prefix = f"{root.rstrip('/')}/" if root else ""
    missing = [
        row
        for row in rows
        if str(row["path"]) not in present and (not prefix or str(row["path"]).startswith(prefix))
    ]
    if missing:
        connection.executemany(
            "UPDATE source_items SET missing_at = ? WHERE id = ?",
            [(now, int(row["id"])) for row in missing],
        )
    return tuple(replace(_item(row), missing_at=now) for row in missing)


def _hash_taken(
    connection: Connection,
    language: str,
    profile_id: str,
    sha256: str,
    *,
    exclude: int | None = None,
) -> bool:
    """Return whether another row of the profile already has this hash."""
    if exclude is None:
        row = connection.execute(
            """
            SELECT 1 FROM source_items
            WHERE language = ? AND profile_id = ? AND sha256 = ?
            """,
            (language, profile_id, sha256),
        ).fetchone()
    else:
        row = connection.execute(
            """
            SELECT 1 FROM source_items
            WHERE language = ? AND profile_id = ? AND sha256 = ? AND id <> ?
            """,
            (language, profile_id, sha256, exclude),
        ).fetchone()
    return row is not None


def _item(row: Row) -> SourceItem:
    """Build one source record from a port row."""
    return SourceItem(
        id=int(row["id"]),
        language=str(row["language"]),
        profile_id=str(row["profile_id"]),
        path=str(row["path"]),
        sha256=str(row["sha256"]),
        size=int(row["size"]),
        duration=float(row["duration"]),
        added_at=str(row["added_at"]),
        used_at=None if row["used_at"] is None else str(row["used_at"]),
        use_count=int(row["use_count"]),
        missing_at=None if row["missing_at"] is None else str(row["missing_at"]),
    )


__all__ = ["mark_missing", "mark_used", "select_next", "upsert_import"]
