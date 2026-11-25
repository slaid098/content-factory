"""Profile-level refill settings stored in the operational database.

Every profile may override the global ``generator.target`` and
``generator.refill_at``. A missing row, or a ``NULL`` column, means the
profile is disabled or follows the global value. The photo goal and threshold
have no global fallback: their default ``0/0`` keeps photo refill off.
"""

from __future__ import annotations

from content_factory.db.connection import Connection
from content_factory.schemas.profiles.refill import ProfileRefill

_UPSERT = """
INSERT INTO profile_settings (
    profile_id, refill_enabled, target, refill_at, photo_target, photo_refill_at, updated_at
)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(profile_id) DO UPDATE SET
    refill_enabled = excluded.refill_enabled,
    target = excluded.target,
    refill_at = excluded.refill_at,
    photo_target = excluded.photo_target,
    photo_refill_at = excluded.photo_refill_at,
    updated_at = excluded.updated_at
"""


def get_refill(connection: Connection, profile_id: str) -> ProfileRefill:
    """Read the refill settings of one profile.

    Args:
        connection: Open connection to the operational database.
        profile_id: Profile identifier such as ``dating/profile_2``.

    Returns:
        Stored settings or ``ProfileRefill(False, None, None)`` when the row
        does not exist; photo refill is then off with a zero pair.
    """
    row = connection.execute(
        """
        SELECT refill_enabled, target, refill_at, photo_target, photo_refill_at
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
        photo_target=int(row[3]),
        photo_refill_at=int(row[4]),
    )


def upsert_refill(
    connection: Connection,
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
        (
            profile_id,
            int(refill.enabled),
            refill.target,
            refill.refill_at,
            refill.photo_target,
            refill.photo_refill_at,
            now,
        ),
    )


__all__ = ["ProfileRefill", "get_refill", "upsert_refill"]
