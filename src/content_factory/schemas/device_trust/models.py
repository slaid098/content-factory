"""Trusted-device access models.

The capsule returns these immutable views instead of database rows: the hashes
of the one-time link and of the device cookie never leave the capsule, so a
dataclass can be printed or logged without leaking a credential.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

DeviceStatus = Literal["pending", "active", "revoked", "expired"]


@dataclass(frozen=True, slots=True)
class IssuedDeviceLink:
    """A freshly issued one-time link.

    ``token`` is the raw link token and is shown to the operator exactly once;
    only its SHA-256 digest is stored. ``link_expires_at`` is the moment the
    link stops working and ``expires_at`` the moment the device stops working.
    """

    device_id: str
    label: str
    token: str
    link_expires_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class RedeemedDevice:
    """A device that just consumed a one-time link.

    ``token`` becomes the ``device_token`` cookie value; the row keeps only its
    SHA-256 digest.
    """

    device_id: str
    label: str
    token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class TrustedDeviceInfo:
    """Safe view of one trusted-device row (no hashes, no raw tokens)."""

    id: str
    label: str
    status: DeviceStatus
    created_at: datetime
    activated_at: datetime | None
    last_seen_at: datetime | None
    revoked_at: datetime | None
    expires_at: datetime


__all__ = [
    "DeviceStatus",
    "IssuedDeviceLink",
    "RedeemedDevice",
    "TrustedDeviceInfo",
]
