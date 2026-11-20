"""Sidecar titles taken from the supplier database."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from loguru import logger

from content_factory.db.connection import connect_readonly

_TITLE_LIMIT = 100
_HASHTAG = re.compile(r"#\S+")
_FALLBACKS = {"ru": "Видео"}
_DEFAULT_FALLBACK = "Video"


def normalize_title(description: str | None, language: str) -> str:
    """Build the sidecar title from a TikTok description.

    TikTok hashtags are removed so the channel keeps its own tags, whitespace
    is collapsed, and long titles are cut to 100 characters with an ellipsis.

    Args:
        description: Raw ``videos.description`` value, if the row exists.
        language: Delivery language used to pick the fallback title.

    Returns:
        A non-empty title of at most 100 characters.
    """
    text = " ".join((description or "").split())
    text = " ".join(_HASHTAG.sub(" ", text).split())
    if not text:
        return _FALLBACKS.get(language, _DEFAULT_FALLBACK)
    if len(text) > _TITLE_LIMIT:
        text = text[: _TITLE_LIMIT - 1].rstrip() + "…"
    return text


def load_descriptions(db_path: Path, hashes: Sequence[str]) -> dict[str, str]:
    """Read TikTok descriptions for downloaded files by their SHA-256.

    A missing or unreadable database must not stop delivery: the caller falls
    back to a generic title for every file.

    Args:
        db_path: Path to the operational SQLite database.
        hashes: SHA-256 file names without the ``.mp4`` suffix.

    Returns:
        Mapping from SHA-256 to the stored description.
    """
    if not hashes or not db_path.is_file():
        return {}
    placeholders = ", ".join("?" for _ in hashes)
    query = (
        "SELECT downloads.sha256 AS sha256, videos.description AS description "
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
            "Не удалось прочитать описания видео, используются fallback-заголовки"
        )
        return {}
    return {str(row["sha256"]): str(row["description"] or "") for row in rows}
