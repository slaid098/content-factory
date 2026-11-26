"""Trusted-device rows of the access gate.

Every statement goes through the shared connection port, so SQLite and
PostgreSQL behave the same. The table stores only SHA-256 digests of the
one-time link and of the device cookie; the raw values never reach it.
"""

from __future__ import annotations

from dataclasses import dataclass

from content_factory.db.connection import Connection
from content_factory.schemas.database.port import Row

_COLUMNS = (
    "id, label, link_hash, link_expires_at, token_hash, created_at, "
    "activated_at, last_seen_at, revoked_at, expires_at"
)

_INSERT = """
INSERT INTO trusted_devices (
    id, label, link_hash, link_expires_at, token_hash,
    created_at, activated_at, last_seen_at, revoked_at, expires_at
) VALUES (?, ?, ?, ?, NULL, ?, NULL, NULL, NULL, ?)
"""

# The conditional update is the redemption: of two concurrent requests exactly
# one matches an unused, unrevoked, unexpired link and unexpired device.
_ACTIVATE = """
UPDATE trusted_devices
SET token_hash = ?, link_hash = NULL, link_expires_at = NULL,
    activated_at = ?, last_seen_at = ?
WHERE link_hash = ?
  AND token_hash IS NULL
  AND revoked_at IS NULL
  AND link_expires_at > ?
  AND expires_at > ?
"""

_REVOKE = """
UPDATE trusted_devices SET revoked_at = ?
WHERE id = ? AND revoked_at IS NULL
"""


@dataclass(frozen=True, slots=True)
class TrustedDeviceRow:
    """One ``trusted_devices`` row exactly as stored."""

    id: str
    label: str
    link_hash: str | None
    link_expires_at: str | None
    token_hash: str | None
    created_at: str
    activated_at: str | None
    last_seen_at: str | None
    revoked_at: str | None
    expires_at: str


def insert_pending(
    connection: Connection,
    *,
    device_id: str,
    label: str,
    link_hash: str,
    link_expires_at: str,
    created_at: str,
    expires_at: str,
) -> None:
    """Insert a pending device that waits for its one-time link.

    Args:
        connection: Open connection to the operational database.
        device_id: ``uuid4().hex`` of the new device.
        label: Operator-facing device name.
        link_hash: SHA-256 digest of the raw link token.
        link_expires_at: ISO 8601 UTC moment the link stops working.
        created_at: ISO 8601 UTC moment of issuance.
        expires_at: ISO 8601 UTC moment the device stops working.
    """
    connection.execute(
        _INSERT,
        (
            device_id,
            label,
            link_hash,
            link_expires_at,
            created_at,
            expires_at,
        ),
    )


def find_pending(connection: Connection, link_hash: str) -> TrustedDeviceRow | None:
    """Return the never-activated row of a link digest, or ``None``."""
    row = connection.execute(
        f"SELECT {_COLUMNS} FROM trusted_devices WHERE link_hash = ? AND token_hash IS NULL",
        (link_hash,),
    ).fetchone()
    return None if row is None else _row(row)


def activate(
    connection: Connection,
    *,
    link_hash: str,
    token_hash: str,
    now: str,
) -> TrustedDeviceRow | None:
    """Atomically swap a link digest for a device-token digest.

    The update matches only an unused, unrevoked, unexpired link of an
    unexpired device, so of two concurrent redemptions exactly one wins.

    Args:
        connection: Open connection to the operational database.
        link_hash: SHA-256 digest of the raw link token.
        token_hash: SHA-256 digest of the new raw device token.
        now: ISO 8601 UTC moment of redemption.

    Returns:
        The activated row, or ``None`` when the link no longer works.
    """
    cursor = connection.execute(
        _ACTIVATE,
        (token_hash, now, now, link_hash, now, now),
    )
    if cursor.rowcount == 0:
        return None
    return _find_by(connection, "token_hash", token_hash)


def find_active(connection: Connection, token_hash: str) -> TrustedDeviceRow | None:
    """Return the row of a device-token digest, or ``None``.

    Revocation and expiry are checked by the service, which owns the clock.
    """
    return _find_by(connection, "token_hash", token_hash)


def touch_last_seen(connection: Connection, device_id: str, now: str) -> None:
    """Refresh ``last_seen_at`` of one device."""
    connection.execute(
        "UPDATE trusted_devices SET last_seen_at = ? WHERE id = ?",
        (now, device_id),
    )


def list_rows(connection: Connection) -> list[TrustedDeviceRow]:
    """Return every device, oldest first."""
    rows = connection.execute(
        f"SELECT {_COLUMNS} FROM trusted_devices ORDER BY created_at, id"
    ).fetchall()
    return [_row(row) for row in rows]


def get_row(connection: Connection, device_id: str) -> TrustedDeviceRow | None:
    """Return one device by id, or ``None``."""
    return _find_by(connection, "id", device_id)


def revoke(connection: Connection, device_id: str, now: str) -> bool:
    """Mark a device revoked and report whether the row exists.

    A repeated call for an already revoked device changes nothing and still
    reports ``True``.
    """
    cursor = connection.execute(_REVOKE, (now, device_id))
    if cursor.rowcount > 0:
        return True
    return get_row(connection, device_id) is not None


def _find_by(connection: Connection, column: str, value: str) -> TrustedDeviceRow | None:
    """Return the single row whose validated column equals *value*."""
    if column not in {"id", "token_hash"}:
        raise ValueError(f"недопустимая колонка: {column!r}")
    row = connection.execute(
        f"SELECT {_COLUMNS} FROM trusted_devices WHERE {column} = ?",
        (value,),
    ).fetchone()
    return None if row is None else _row(row)


def _row(row: Row) -> TrustedDeviceRow:
    """Build one device row from a database row."""
    return TrustedDeviceRow(
        id=str(row["id"]),
        label=str(row["label"]),
        link_hash=None if row["link_hash"] is None else str(row["link_hash"]),
        link_expires_at=(None if row["link_expires_at"] is None else str(row["link_expires_at"])),
        token_hash=None if row["token_hash"] is None else str(row["token_hash"]),
        created_at=str(row["created_at"]),
        activated_at=None if row["activated_at"] is None else str(row["activated_at"]),
        last_seen_at=None if row["last_seen_at"] is None else str(row["last_seen_at"]),
        revoked_at=None if row["revoked_at"] is None else str(row["revoked_at"]),
        expires_at=str(row["expires_at"]),
    )


__all__ = [
    "TrustedDeviceRow",
    "activate",
    "find_active",
    "find_pending",
    "get_row",
    "insert_pending",
    "list_rows",
    "revoke",
    "touch_last_seen",
]
