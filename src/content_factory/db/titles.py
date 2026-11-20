"""Persistence of generated video titles."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from loguru import logger

from content_factory.db.connection import connect_readonly

_SCHEMA = """
CREATE TABLE IF NOT EXISTS titles (
    sha256 TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    norm TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
)
"""

_INDEX = """
CREATE INDEX IF NOT EXISTS idx_titles_created_at ON titles(created_at DESC)
"""

_BATCH_SIZE = 500


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the titles table and its index when missing.

    Args:
        conn: Open connection to the operational database.
    """
    conn.execute(_SCHEMA)
    conn.execute(_INDEX)
    conn.commit()


def load_titles(db_path: Path, hashes: Sequence[str]) -> dict[str, str]:
    """Read non-empty titles of downloaded files by their SHA-256.

    A missing database, a database without the titles table or an unreadable
    database must not stop the flow: the caller treats every file as untitled.

    Args:
        db_path: Path to the operational SQLite database.
        hashes: SHA-256 file names without the ``.mp4`` suffix.

    Returns:
        Mapping from SHA-256 to the stored title.
    """
    if not hashes or not db_path.is_file():
        return {}
    titles: dict[str, str] = {}
    try:
        connection = connect_readonly(db_path)
        try:
            for start in range(0, len(hashes), _BATCH_SIZE):
                batch = list(hashes[start : start + _BATCH_SIZE])
                titles.update(_load_batch(connection, batch))
        finally:
            connection.close()
    except sqlite3.OperationalError as ex:
        if "no such table" in str(ex).lower():
            return {}
        _log_read_error(ex)
        return {}
    except (OSError, sqlite3.Error) as ex:
        _log_read_error(ex)
        return {}
    return titles


def save_title(
    conn: sqlite3.Connection,
    sha256: str,
    title: str,
    norm: str,
    created_at: str,
) -> bool:
    """Insert one title unless its hash or normalized key is already taken.

    Args:
        conn: Open connection to the operational database.
        sha256: SHA-256 file name without the ``.mp4`` suffix.
        title: Cleaned title text.
        norm: Normalized uniqueness key of the title.
        created_at: ISO 8601 creation moment.

    Returns:
        ``True`` when the row was actually inserted.
    """
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO titles (sha256, title, norm, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (sha256, title, norm, created_at),
    )
    conn.commit()
    return cursor.rowcount > 0


def recent_titles(db_path: Path, limit: int = 40) -> tuple[str, ...]:
    """Read the newest titles, newest first.

    Args:
        db_path: Path to the operational SQLite database.
        limit: Maximum number of titles to return.

    Returns:
        Stored titles, newest first; empty when the database or table is absent.
    """
    if limit <= 0 or not db_path.is_file():
        return ()
    try:
        connection = connect_readonly(db_path)
        try:
            rows = connection.execute(
                """
                SELECT title FROM titles
                WHERE title <> ''
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.OperationalError as ex:
        if "no such table" in str(ex).lower():
            return ()
        _log_read_error(ex)
        return ()
    except (OSError, sqlite3.Error) as ex:
        _log_read_error(ex)
        return ()
    return tuple(str(row["title"]) for row in rows)


def _load_batch(connection: sqlite3.Connection, hashes: Sequence[str]) -> dict[str, str]:
    """Read one batch of titles, one placeholder per hash."""
    placeholders = ", ".join("?" for _ in hashes)
    rows = connection.execute(
        f"""
        SELECT sha256, title FROM titles
        WHERE sha256 IN ({placeholders}) AND title <> ''
        """,
        list(hashes),
    ).fetchall()
    return {str(row["sha256"]): cast(str, row["title"]) for row in rows}


def _log_read_error(error: Exception) -> None:
    """Log an unreadable titles database without stopping the caller."""
    logger.bind(error=error, error_type=type(error).__name__).warning(
        "Не удалось прочитать названия видео"
    )
