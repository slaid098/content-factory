"""Profile-level refill settings stored in SQLite.

Every profile may override the global ``generator.target`` and
``generator.refill_at``. A missing row, or a ``NULL`` column, means the
profile is disabled or follows the global value.
"""

from __future__ import annotations

import sqlite3

from content_factory.schemas.profiles.refill import ProfileRefill

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profile_settings (
    profile_id TEXT PRIMARY KEY,
    refill_enabled INTEGER NOT NULL DEFAULT 0,
    target INTEGER,
    refill_at INTEGER,
    updated_at TEXT NOT NULL
)
"""

_UPSERT = """
INSERT INTO profile_settings (profile_id, refill_enabled, target, refill_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(profile_id) DO UPDATE SET
    refill_enabled = excluded.refill_enabled,
    target = excluded.target,
    refill_at = excluded.refill_at,
    updated_at = excluded.updated_at
"""


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the profile settings table when it is missing.

    Args:
        connection: Open connection to the operational database.
    """
    connection.execute(_SCHEMA)
    connection.commit()


def get_refill(connection: sqlite3.Connection, profile_id: str) -> ProfileRefill:
    """Read the refill settings of one profile.

    Args:
        connection: Open connection to the operational database.
        profile_id: Profile identifier such as ``dating/profile_2``.

    Returns:
        Stored settings or ``ProfileRefill(False, None, None)`` when the row
        does not exist.
    """
    row = connection.execute(
        """
        SELECT refill_enabled, target, refill_at
        FROM profile_settings
        WHERE profile_id = ?
        """,
        (profile_id,),
    ).fetchone()
    if row is None:
        return ProfileRefill(False, None, None)
    return ProfileRefill(
        enabled=bool(row[0]),
        target=None if row[1] is None else int(row[1]),
        refill_at=None if row[2] is None else int(row[2]),
    )


def upsert_refill(
    connection: sqlite3.Connection,
    profile_id: str,
    refill: ProfileRefill,
    now: str,
) -> None:
    """Insert or replace the refill settings of one profile.

    Args:
        connection: Open connection to the operational database.
        profile_id: Profile identifier such as ``dating/profile_2``.
        refill: New settings; ``None`` values keep the global defaults.
        now: ISO 8601 moment of the change.
    """
    connection.execute(
        _UPSERT,
        (profile_id, int(refill.enabled), refill.target, refill.refill_at, now),
    )


__all__ = ["ProfileRefill", "ensure_schema", "get_refill", "upsert_refill"]
