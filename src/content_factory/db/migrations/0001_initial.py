"""Initial schema of the operational database.

The migration creates the final shape of all seven tables and adds the columns
that older dev SQLite files miss. It is safe to run on a fresh database and on
a legacy one: table and index creation use ``IF NOT EXISTS``, and missing
columns are checked through the connection port before ``ALTER TABLE``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from content_factory.schemas.database.port import Connection

VERSION = "0001"

_STOCK_ITEMS = """
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

_ADD_STOCK_CONTENT_TYPE = (
    "ALTER TABLE stock_items ADD COLUMN content_type TEXT NOT NULL DEFAULT 'video'"
)

_LEGACY_STOCK_INDEX_DROP = "DROP INDEX IF EXISTS idx_stock_items_language_delivered"

_STOCK_INDEXES = (
    """
    CREATE INDEX IF NOT EXISTS idx_stock_items_language_type_delivered
    ON stock_items(language, content_type, delivered_at)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_stock_items_profile_delivered
    ON stock_items(profile_id, delivered_at)
    """,
)

_TITLES = """
CREATE TABLE IF NOT EXISTS titles (
    sha256 TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    norm TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
)
"""

_TITLES_INDEX = """
CREATE INDEX IF NOT EXISTS idx_titles_created_at ON titles(created_at DESC)
"""

_PROFILE_SETTINGS = """
CREATE TABLE IF NOT EXISTS profile_settings (
    profile_id TEXT PRIMARY KEY,
    refill_enabled INTEGER NOT NULL DEFAULT 0,
    target INTEGER,
    refill_at INTEGER,
    updated_at TEXT NOT NULL
)
"""

_RUNTIME_STATE = """
CREATE TABLE IF NOT EXISTS runtime_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    last_production_at TEXT,
    captcha_blocked_until TEXT,
    captcha_streak INTEGER NOT NULL DEFAULT 0,
    zero_runs INTEGER NOT NULL DEFAULT 0,
    last_alert_at TEXT,
    last_alert_kind TEXT,
    updated_at TEXT NOT NULL
)
"""

_ALERT_COOLDOWNS = """
CREATE TABLE IF NOT EXISTS alert_cooldowns (
    kind TEXT PRIMARY KEY,
    sent_at TEXT NOT NULL
)
"""

_VIDEOS = """
CREATE TABLE IF NOT EXISTS videos (
    video_id TEXT PRIMARY KEY,
    language TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL,
    author TEXT NOT NULL,
    query TEXT NOT NULL DEFAULT '',
    views INTEGER,
    followers INTEGER,
    duration INTEGER,
    width INTEGER,
    height INTEGER,
    create_time INTEGER,
    description TEXT NOT NULL DEFAULT '',
    is_aigc INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'found'
        CHECK (status IN ('found', 'approved', 'rejected', 'downloaded', 'failed')),
    reject_reason TEXT,
    discovered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_ADD_VIDEO_LANGUAGE = "ALTER TABLE videos ADD COLUMN language TEXT NOT NULL DEFAULT ''"

_DOWNLOADS = """
CREATE TABLE IF NOT EXISTS downloads (
    video_id TEXT PRIMARY KEY REFERENCES videos(video_id),
    sha256 TEXT NOT NULL UNIQUE,
    path TEXT NOT NULL,
    size INTEGER NOT NULL,
    downloaded_at TEXT NOT NULL
)
"""

_VIDEO_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status)",
    "CREATE INDEX IF NOT EXISTS idx_videos_language_status ON videos(language, status)",
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_videos_author_downloaded
    ON videos(author) WHERE status = 'downloaded'
    """,
)


def apply(connection: Connection) -> None:
    """Create the final schema and upgrade legacy SQLite tables.

    Args:
        connection: Open connection whose backend is SQLite or PostgreSQL.
    """
    connection.execute(_STOCK_ITEMS)
    _add_missing_column(
        connection,
        table="stock_items",
        column="content_type",
        statement=_ADD_STOCK_CONTENT_TYPE,
    )
    connection.execute(_LEGACY_STOCK_INDEX_DROP)
    for statement in _STOCK_INDEXES:
        connection.execute(statement)

    connection.execute(_TITLES)
    connection.execute(_TITLES_INDEX)
    connection.execute(_PROFILE_SETTINGS)
    connection.execute(_RUNTIME_STATE)
    connection.execute(_ALERT_COOLDOWNS)

    connection.execute(_VIDEOS)
    _add_missing_column(
        connection,
        table="videos",
        column="language",
        statement=_ADD_VIDEO_LANGUAGE,
    )
    connection.execute(_DOWNLOADS)
    for statement in _VIDEO_INDEXES:
        connection.execute(statement)


def _add_missing_column(
    connection: Connection,
    *,
    table: str,
    column: str,
    statement: str,
) -> None:
    """Add one column unless the table already has it."""
    if column not in connection.columns(table):
        connection.execute(statement)


__all__ = ["VERSION", "apply"]
