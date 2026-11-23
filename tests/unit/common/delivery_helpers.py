"""Shared builders for delivery tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import UUID

from content_factory.db.titles import ensure_schema
from content_factory.services.supplier.repository import Repository
from content_factory.services.titles.text import normalize_title_key

REQUEST_ID = UUID("11111111-1111-4111-8111-111111111111")
REQUEST_ID_TEXT = str(REQUEST_ID)


def touch_mp4(path: Path, data: bytes = b"video") -> Path:
    """Create an mp4 file with the given bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def add_downloaded_video(
    db_path: Path,
    sha256: str,
    description: str,
    *,
    video_id: str | None = None,
    query: str = "",
    language: str = "ru",
) -> None:
    """Insert a downloaded video row with its TikTok description and topic."""
    Repository(db_path).init_schema()
    identifier = video_id or f"video-{sha256[:16]}"
    author = f"author-{sha256[:8]}"
    connection = sqlite3.connect(db_path)
    try:
        connection.execute(
            """
            INSERT INTO videos (
                video_id, language, url, author, query, description, status,
                discovered_at, updated_at
            ) VALUES (?, ?, 'https://example.test/video', ?, ?, ?,
                      'downloaded', '2026-05-01T00:00:00+00:00',
                      '2026-05-01T00:00:00+00:00')
            """,
            (identifier, language, author, query, description),
        )
        connection.execute(
            """
            INSERT INTO downloads (video_id, sha256, path, size, downloaded_at)
            VALUES (?, ?, ?, ?, '2026-05-01T00:00:00+00:00')
            """,
            (identifier, sha256, f"/content/{language}/{sha256}.mp4", 5),
        )
        connection.commit()
    finally:
        connection.close()


def add_title(
    db_path: Path,
    sha256: str,
    title: str,
    *,
    norm: str | None = None,
    created_at: str | None = None,
) -> None:
    """Insert a generated title row for delivery and statistics tests."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    try:
        ensure_schema(connection)
        connection.execute(
            """
            INSERT INTO titles (sha256, title, norm, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                sha256,
                title,
                norm or normalize_title_key(title),
                created_at or "2026-05-01T00:00:00+00:00",
            ),
        )
        connection.commit()
    finally:
        connection.close()
