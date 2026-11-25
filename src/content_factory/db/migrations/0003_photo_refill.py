"""Photo refill settings of generator profiles.

The migration adds ``photo_target`` and ``photo_refill_at`` to
``profile_settings``. Both columns are ``NOT NULL`` with a zero default, so an
existing row keeps the current behaviour: photo refill is off. Each column is
added only when the table does not have it, which makes the migration safe on
SQLite and PostgreSQL databases that already hold profiles.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from content_factory.schemas.database.port import Connection

VERSION = "0003"

_PHOTO_TARGET = "ALTER TABLE profile_settings ADD COLUMN photo_target INTEGER NOT NULL DEFAULT 0"
_PHOTO_REFILL_AT = (
    "ALTER TABLE profile_settings ADD COLUMN photo_refill_at INTEGER NOT NULL DEFAULT 0"
)


def apply(connection: Connection) -> None:
    """Add the photo refill columns the settings table is missing.

    Args:
        connection: Open connection whose backend is SQLite or PostgreSQL.
    """
    columns = connection.columns("profile_settings")
    if "photo_target" not in columns:
        connection.execute(_PHOTO_TARGET)
    if "photo_refill_at" not in columns:
        connection.execute(_PHOTO_REFILL_AT)


__all__ = ["VERSION", "apply"]
