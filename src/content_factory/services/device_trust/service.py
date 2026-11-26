"""Trusted-device access capsule.

A one-time link turns a browser into a trusted device: :func:`issue_link`
stores only the link digest, :func:`redeem_link` atomically swaps it for a
device-token digest, and :func:`authenticate` answers the per-request gate.
Raw link and device tokens are generated here and never stored or logged;
callers get them once through the returned dataclasses.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from content_factory.db.connection import connect
from content_factory.db.device_trust import (
    activate,
    find_active,
    find_pending,
    insert_pending,
    list_rows,
    revoke,
    touch_last_seen,
)
from content_factory.schemas.device_trust.models import (
    DeviceStatus,
    IssuedDeviceLink,
    RedeemedDevice,
    TrustedDeviceInfo,
)

if TYPE_CHECKING:
    from content_factory.db.device_trust import TrustedDeviceRow

# The label is operator-facing only; 150 is the column width.
_LABEL_MAX_LENGTH = 150
# Bounds of the operator-facing TTLs; the settings fields repeat them.
_LINK_TTL_HOURS_MIN = 1
_LINK_TTL_HOURS_MAX = 720
_DEVICE_TTL_DAYS_MIN = 1
_DEVICE_TTL_DAYS_MAX = 3650
# Refresh last_seen_at at most once an hour so the gate stays write-light.
_LAST_SEEN_REFRESH_SECONDS = 3600
# token_urlsafe(32) yields 43 URL-safe characters and 256 bits of entropy.
_TOKEN_BYTES = 32


def issue_link(
    db_path: Path,
    label: str,
    link_ttl_hours: int,
    device_ttl_days: int,
    *,
    now: datetime | None = None,
) -> IssuedDeviceLink:
    """Create a pending device row and return its one-time link token.

    Args:
        db_path: Operational database of the service.
        label: Operator-facing device name (trimmed, 1..150 characters).
        link_ttl_hours: Link lifetime in hours, 1..720.
        device_ttl_days: Device lifetime in days, 1..3650.
        now: Clock override for tests; defaults to the current UTC moment.

    Returns:
        The raw token (store it nowhere) and both expiry moments.

    Raises:
        ValueError: The label or one of the TTLs violates its contract.
    """
    clean_label = _clean_label(label)
    _check_link_ttl(link_ttl_hours)
    _check_device_ttl(device_ttl_days)
    moment = _moment(now)
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    device_id = uuid4().hex
    link_expires_at = moment + timedelta(hours=link_ttl_hours)
    expires_at = moment + timedelta(days=device_ttl_days)
    connection = connect(db_path)
    try:
        insert_pending(
            connection,
            device_id=device_id,
            label=clean_label,
            link_hash=_hash_token(token),
            link_expires_at=_store(link_expires_at),
            created_at=_store(moment),
            expires_at=_store(expires_at),
        )
        connection.commit()
    finally:
        connection.close()
    return IssuedDeviceLink(
        device_id=device_id,
        label=clean_label,
        token=token,
        link_expires_at=link_expires_at,
        expires_at=expires_at,
    )


def redeem_link(
    db_path: Path,
    token: str,
    *,
    now: datetime | None = None,
) -> RedeemedDevice | None:
    """Consume a pending link and return the new device token.

    A read-only lookup rejects a token that matches no pending row before the
    conditional update, so a garbage or reused ``/join/<token>`` leaves the
    table alone. The update still owns the race between two browsers opening
    the same link.

    Args:
        db_path: Operational database of the service.
        token: Raw one-time link token from the ``/join/{token}`` path.
        now: Clock override for tests; defaults to the current UTC moment.

    Returns:
        The device id, label and raw device token, or ``None`` when the link is
        unknown, already used, expired or revoked.
    """
    if not token:
        return None
    moment = _moment(now)
    link_hash = _hash_token(token)
    device_token = secrets.token_urlsafe(_TOKEN_BYTES)
    connection = connect(db_path)
    try:
        if find_pending(connection, link_hash) is None:
            return None
        row = activate(
            connection,
            link_hash=link_hash,
            token_hash=_hash_token(device_token),
            now=_store(moment),
        )
        if row is None:
            connection.rollback()
            return None
        connection.commit()
    finally:
        connection.close()
    return RedeemedDevice(
        device_id=row.id,
        label=row.label,
        token=device_token,
        expires_at=_parse_moment(row.expires_at),
    )


def authenticate(
    db_path: Path,
    token: str,
    *,
    now: datetime | None = None,
) -> TrustedDeviceInfo | None:
    """Return the active device for a cookie token and refresh its last seen.

    Args:
        db_path: Operational database of the service.
        token: Raw ``device_token`` cookie value.
        now: Clock override for tests; defaults to the current UTC moment.

    Returns:
        The device view for an active row, or ``None`` for an unknown,
        revoked, never-activated or expired device.
    """
    if not token:
        return None
    moment = _moment(now)
    connection = connect(db_path)
    try:
        row = find_active(connection, _hash_token(token))
        if row is None or _status(row, moment) != "active":
            return None
        touched = None
        if _needs_touch(row, moment):
            touch_last_seen(connection, row.id, _store(moment))
            connection.commit()
            touched = _store(moment)
        return _info(row, moment, last_seen_at=touched)
    finally:
        connection.close()


def list_devices(db_path: Path, *, now: datetime | None = None) -> list[TrustedDeviceInfo]:
    """Return every device, oldest first, for the operator CLI."""
    moment = _moment(now)
    connection = connect(db_path)
    try:
        rows = list_rows(connection)
    finally:
        connection.close()
    return [_info(row, moment) for row in rows]


def revoke_device(db_path: Path, device_id: str, *, now: datetime | None = None) -> bool:
    """Revoke a device (idempotent) and report whether the row exists.

    Args:
        db_path: Operational database of the service.
        device_id: ``trusted_devices.id``.
        now: Clock override for tests; defaults to the current UTC moment.

    Returns:
        ``True`` when the row exists; ``False`` for an unknown id. A repeated
        call for an already revoked device still returns ``True``.
    """
    moment = _moment(now)
    connection = connect(db_path)
    try:
        found = revoke(connection, device_id, _store(moment))
        connection.commit()
    finally:
        connection.close()
    return found


def database_ready(db_path: Path) -> None:
    """Run ``SELECT 1`` so the deploy probe sees a live database.

    The probe must not read stock data, only prove that the gate can reach the
    database at all.

    Raises:
        DatabaseError: The database cannot be opened or queried.
    """
    connection = connect(db_path)
    try:
        connection.execute("SELECT 1").fetchone()
    finally:
        connection.close()


def _moment(now: datetime | None) -> datetime:
    """Return an aware UTC moment, defaulting to the current one."""
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)


def _store(moment: datetime) -> str:
    """Render an aware UTC moment as the stored ISO 8601 text."""
    return moment.isoformat()


def _parse_moment(value: str) -> datetime:
    """Read a stored ISO 8601 moment as aware UTC."""
    return _moment(datetime.fromisoformat(value))


def _hash_token(token: str) -> str:
    """Return the SHA-256 hex digest stored in place of a raw token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _clean_label(label: str) -> str:
    """Trim the operator label and enforce the column contract.

    Raises:
        ValueError: The trimmed label is empty or longer than 150 characters.
    """
    clean = label.strip()
    if not 1 <= len(clean) <= _LABEL_MAX_LENGTH:
        raise ValueError(f"label must be 1..{_LABEL_MAX_LENGTH} characters")
    return clean


def _check_link_ttl(hours: int) -> None:
    """Reject a link TTL outside 1..720 hours."""
    if not _LINK_TTL_HOURS_MIN <= hours <= _LINK_TTL_HOURS_MAX:
        raise ValueError(f"link_ttl_hours must be {_LINK_TTL_HOURS_MIN}..{_LINK_TTL_HOURS_MAX}")


def _check_device_ttl(days: int) -> None:
    """Reject a device TTL outside 1..3650 days."""
    if not _DEVICE_TTL_DAYS_MIN <= days <= _DEVICE_TTL_DAYS_MAX:
        raise ValueError(f"device_ttl_days must be {_DEVICE_TTL_DAYS_MIN}..{_DEVICE_TTL_DAYS_MAX}")


def _status(row: TrustedDeviceRow, moment: datetime) -> DeviceStatus:
    """Derive the lifecycle status of a row at *moment*."""
    if row.revoked_at is not None:
        return "revoked"
    if _parse_moment(row.expires_at) <= moment:
        return "expired"
    if row.token_hash is None:
        return "pending"
    return "active"


def _info(
    row: TrustedDeviceRow,
    moment: datetime,
    *,
    last_seen_at: str | None = None,
) -> TrustedDeviceInfo:
    """Map a database row to the safe public view."""
    last_seen = row.last_seen_at if last_seen_at is None else last_seen_at
    return TrustedDeviceInfo(
        id=row.id,
        label=row.label,
        status=_status(row, moment),
        created_at=_parse_moment(row.created_at),
        activated_at=None if row.activated_at is None else _parse_moment(row.activated_at),
        last_seen_at=None if last_seen is None else _parse_moment(last_seen),
        revoked_at=None if row.revoked_at is None else _parse_moment(row.revoked_at),
        expires_at=_parse_moment(row.expires_at),
    )


def _needs_touch(row: TrustedDeviceRow, moment: datetime) -> bool:
    """Return whether ``last_seen_at`` is missing or older than an hour."""
    if row.last_seen_at is None:
        return True
    age = moment - _parse_moment(row.last_seen_at)
    return age > timedelta(seconds=_LAST_SEEN_REFRESH_SECONDS)


__all__ = [
    "authenticate",
    "database_ready",
    "issue_link",
    "list_devices",
    "redeem_link",
    "revoke_device",
]
