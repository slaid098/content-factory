"""Read video metadata from the database for delivery and title generation."""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from loguru import logger

from content_factory.db.connection import connect_readonly
from content_factory.schemas.content.video import VideoMeta


def load_video_meta(db_path: Path, hashes: Sequence[str]) -> dict[str, VideoMeta]:
    """Read descriptions, topics and durations for downloaded files by SHA-256.

    A missing or unreadable database must not stop the caller: delivery finds
    no topic matches, and title generation uses an empty description.

    Args:
        db_path: Path to the operational SQLite database.
        hashes: SHA-256 file names without the ``.mp4`` suffix.

    Returns:
        Mapping from SHA-256 to the stored video metadata.
    """
    if not hashes or not db_path.is_file():
        return {}
    placeholders = ", ".join("?" for _ in hashes)
    query = (
        "SELECT downloads.sha256 AS sha256, videos.description AS description, "
        "videos.query AS query, videos.duration AS duration "
        "FROM downloads JOIN videos ON videos.video_id = downloads.video_id "
        f"WHERE downloads.sha256 IN ({placeholders})"
    )
    try:
        connection = connect_readonly(db_path)
        try:
            rows = connection.execute(query, list(hashes)).fetchall()
        finally:
            connection.close()
    except (OSError, sqlite3.Error) as ex:
        logger.bind(error=ex, error_type=type(ex).__name__).warning(
            "Не удалось прочитать метаданные видео"
        )
        return {}
    return {
        str(row["sha256"]): VideoMeta(
            description=str(row["description"] or ""),
            query=str(row["query"] or ""),
            duration=None if row["duration"] is None else int(row["duration"]),
        )
        for row in rows
    }
