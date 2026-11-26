"""Column contract of ``trusted_devices`` shared by the migration tests."""

from __future__ import annotations

TRUSTED_DEVICE_COLUMNS = frozenset(
    {
        "id",
        "label",
        "link_hash",
        "link_expires_at",
        "token_hash",
        "created_at",
        "activated_at",
        "last_seen_at",
        "revoked_at",
        "expires_at",
    }
)

__all__ = ["TRUSTED_DEVICE_COLUMNS"]
