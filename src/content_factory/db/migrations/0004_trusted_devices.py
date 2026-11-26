"""Trusted devices that pass the access gate.

The table holds one row per issued device link. Raw link and device tokens are
never stored: the row keeps their SHA-256 digests, and the link digest is
cleared once the browser redeems the link. The statement is portable between
SQLite and PostgreSQL and safe to run on a database that already has the table.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from content_factory.schemas.database.port import Connection

VERSION = "0004"

_TRUSTED_DEVICES = """
CREATE TABLE IF NOT EXISTS trusted_devices (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    link_hash TEXT UNIQUE,
    link_expires_at TEXT,
    token_hash TEXT UNIQUE,
    created_at TEXT NOT NULL,
    activated_at TEXT,
    last_seen_at TEXT,
    revoked_at TEXT,
    expires_at TEXT NOT NULL
)
"""


def apply(connection: Connection) -> None:
    """Create the trusted-device table.

    Args:
        connection: Open connection whose backend is SQLite or PostgreSQL.
    """
    connection.execute(_TRUSTED_DEVICES)


__all__ = ["VERSION", "apply"]
