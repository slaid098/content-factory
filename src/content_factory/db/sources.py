"""Source queue of the uniqueizer: items, cursors and their transitions.

Every statement goes through the shared connection port, so SQLite and
PostgreSQL behave the same. Paths are stored relative to the runtime data
directory in POSIX form; the queue resolves them against ``data_dir`` and the
database never holds video bytes.
"""

from __future__ import annotations

from collections.abc import Sequence

from content_factory.db.connection import Connection
from content_factory.schemas.database.port import Row
from content_factory.schemas.generator.uniqueizer import (
    SourceCandidate,
    SourceImportAction,
    SourceItem,
)

_COLUMNS = (
    "path, language, profile_id, sha256, size, duration, added_at, used_at, missing_at, use_count"
)

_INSERT = """
INSERT INTO source_items (
    path, language, profile_id, sha256, size, duration,
    added_at, used_at, missing_at, use_count
) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, 0)
"""

_REFRESH_CONTENT = """
UPDATE source_items
SET sha256 = ?, size = ?, duration = ?, used_at = NULL, use_count = 0, missing_at = NULL
WHERE path = ?
"""

_REFRESH_METADATA = """
UPDATE source_items
SET size = ?, duration = ?, missing_at = NULL
WHERE path = ?
"""

_SET_CURSOR = """
INSERT INTO source_cursors (language, profile_id, last_path, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(language, profile_id) DO UPDATE SET
    last_path = excluded.last_path,
    updated_at = excluded.updated_at
"""

_BATCH_SIZE = 500


def is_source_path(value: str) -> bool:
    """Return whether *value* is a safe relative POSIX path.

    A stored path must be relative, must not use backslashes and must not
    contain empty, ``.`` or ``..`` segments. Joining such a path to the data
    directory can never leave that directory.

    Args:
        value: Path as stored in ``source_items.path``.

    Returns:
        ``True`` for a safe relative path.
    """
    if not value or "\\" in value or "\x00" in value:
        return False
    if value.startswith("/") or (len(value) >= 2 and value[1] == ":" and value[0].isalpha()):
        return False
    return all(part not in {"", ".", ".."} for part in value.split("/"))


def get_source(connection: Connection, path: str) -> SourceItem | None:
    """Return one source row by its stored path, or ``None``.

    Raises:
        ValueError: The path is absolute, uses backslashes or contains
            ``.``/``..`` segments.
    """
    checked = _checked_path(path)
    row = connection.execute(
        f"SELECT {_COLUMNS} FROM source_items WHERE path = ?",
        (checked,),
    ).fetchone()
    return None if row is None else _item(row)


def list_sources(connection: Connection, language: str, profile_id: str) -> list[SourceItem]:
    """Return every source row of one profile in path order."""
    rows = connection.execute(
        f"""
        SELECT {_COLUMNS} FROM source_items
        WHERE language = ? AND profile_id = ?
        ORDER BY {_path_order(connection)}
        """,
        (language, profile_id),
    ).fetchall()
    return [_item(row) for row in rows]


def list_next_sources(
    connection: Connection,
    language: str,
    profile_id: str,
    after: str | None,
    limit: int = 1,
) -> list[SourceItem]:
    """Return the next source rows after *after* in path order.

    Rows marked missing are skipped, because their file is not on disk.

    Args:
        connection: Open connection to the operational database.
        language: Validated language code of the queue.
        profile_id: Profile identifier such as ``dating/profile_1``.
        after: Last successfully processed path, or ``None`` to start over.
        limit: Maximum number of rows to return, at least one.

    Returns:
        Source rows in path order; an empty list when the queue is exhausted.

    Raises:
        ValueError: ``limit`` is not positive.
    """
    if limit <= 0:
        raise ValueError("limit должен быть больше нуля")
    path = _path_order(connection)
    if after is None:
        rows = connection.execute(
            f"""
            SELECT {_COLUMNS} FROM source_items
            WHERE language = ? AND profile_id = ? AND missing_at IS NULL
            ORDER BY {path}
            LIMIT ?
            """,
            (language, profile_id, limit),
        ).fetchall()
    else:
        rows = connection.execute(
            f"""
            SELECT {_COLUMNS} FROM source_items
            WHERE language = ? AND profile_id = ? AND missing_at IS NULL AND {path} > ?
            ORDER BY {path}
            LIMIT ?
            """,
            (language, profile_id, after, limit),
        ).fetchall()
    return [_item(row) for row in rows]


def upsert_source(
    connection: Connection,
    source: SourceCandidate,
    now: str,
) -> SourceImportAction:
    """Insert a scanned source or refresh the row of the same path.

    A new content hash of an existing path resets ``used_at`` and
    ``use_count``; the same content keeps the usage history and only clears a
    missing mark. A hash already claimed by another path of the same profile
    is a duplicate and changes nothing.

    Args:
        connection: Open connection to the operational database.
        source: Metadata of one scanned video file.
        now: ISO 8601 moment used as ``added_at`` of a new row.

    Returns:
        How the row changed. A row that already holds the same path for
        another language or profile is a conflict and stays untouched.

    Raises:
        ValueError: The path is absolute, uses backslashes or contains
            ``.``/``..`` segments.
    """
    path = _checked_path(source.path)
    existing = get_source(connection, path)
    if existing is not None and (
        existing.language != source.language or existing.profile_id != source.profile_id
    ):
        return SourceImportAction.CONFLICT
    if existing is None:
        if _hash_taken(connection, source):
            return SourceImportAction.DUPLICATE
        connection.execute(
            _INSERT,
            (
                path,
                source.language,
                source.profile_id,
                source.sha256,
                source.size,
                source.duration,
                now,
            ),
        )
        return SourceImportAction.ADDED
    if existing.sha256 != source.sha256:
        if _hash_taken(connection, source):
            return SourceImportAction.DUPLICATE
        connection.execute(
            _REFRESH_CONTENT,
            (source.sha256, source.size, source.duration, path),
        )
        return SourceImportAction.UPDATED
    connection.execute(_REFRESH_METADATA, (source.size, source.duration, path))
    if existing.missing_at is not None:
        return SourceImportAction.RETURNED
    return SourceImportAction.UNCHANGED


def mark_used(connection: Connection, path: str, now: str) -> bool:
    """Record a successful copy and increment the usage counter.

    Returns:
        ``True`` when the row exists and was updated.
    """
    cursor = connection.execute(
        "UPDATE source_items SET used_at = ?, use_count = use_count + 1 WHERE path = ?",
        (now, path),
    )
    return cursor.rowcount > 0


def mark_missing(connection: Connection, paths: Sequence[str], now: str) -> int:
    """Mark rows whose files disappeared from the scan.

    An already marked row keeps its first missing moment.

    Returns:
        Number of rows marked by this call.
    """
    marked = 0
    for batch in _batches(list(paths)):
        placeholders = ", ".join("?" for _ in batch)
        cursor = connection.execute(
            f"""
            UPDATE source_items SET missing_at = ?
            WHERE missing_at IS NULL AND path IN ({placeholders})
            """,
            [now, *batch],
        )
        marked += cursor.rowcount
    return marked


def get_cursor(connection: Connection, language: str, profile_id: str) -> str | None:
    """Return the last processed path of one profile, or ``None``."""
    row = connection.execute(
        """
        SELECT last_path FROM source_cursors
        WHERE language = ? AND profile_id = ?
        """,
        (language, profile_id),
    ).fetchone()
    if row is None:
        return None
    return None if row["last_path"] is None else str(row["last_path"])


def set_cursor(
    connection: Connection,
    language: str,
    profile_id: str,
    last_path: str,
    now: str,
) -> None:
    """Store the last successfully processed path of one profile."""
    connection.execute(_SET_CURSOR, (language, profile_id, last_path, now))


def _checked_path(value: str) -> str:
    """Return a safe stored path or raise ``ValueError``."""
    if not is_source_path(value):
        raise ValueError(f"небезопасный путь источника: {value!r}")
    return value


def _hash_taken(connection: Connection, source: SourceCandidate) -> bool:
    """Return whether another path of the profile already holds the hash."""
    row = connection.execute(
        """
        SELECT 1 FROM source_items
        WHERE language = ? AND profile_id = ? AND sha256 = ? AND path <> ?
        """,
        (source.language, source.profile_id, source.sha256, source.path),
    ).fetchone()
    return row is not None


def _path_order(connection: Connection) -> str:
    """Return a byte-order path expression for stable cross-backend ordering.

    PostgreSQL's default collation depends on the database locale, while
    SQLite compares bytes. The explicit ``C`` collation keeps both backends
    and plain Python string order identical for non-ASCII file names.
    """
    return 'path COLLATE "C"' if connection.dialect == "postgres" else "path"


def _batches(paths: list[str]) -> list[list[str]]:
    """Split paths into bounded parameter lists."""
    return [paths[start : start + _BATCH_SIZE] for start in range(0, len(paths), _BATCH_SIZE)]


def _item(row: Row) -> SourceItem:
    """Build one source row from a database row."""
    return SourceItem(
        path=str(row["path"]),
        language=str(row["language"]),
        profile_id=str(row["profile_id"]),
        sha256=str(row["sha256"]),
        size=int(row["size"]),
        duration=None if row["duration"] is None else float(row["duration"]),
        added_at=str(row["added_at"]),
        used_at=None if row["used_at"] is None else str(row["used_at"]),
        missing_at=None if row["missing_at"] is None else str(row["missing_at"]),
        use_count=int(row["use_count"]),
    )


__all__ = [
    "get_cursor",
    "get_source",
    "is_source_path",
    "list_next_sources",
    "list_sources",
    "mark_missing",
    "mark_used",
    "set_cursor",
    "upsert_source",
]
