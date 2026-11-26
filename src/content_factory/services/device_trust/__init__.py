"""Trusted-device access capsule door."""

from content_factory.services.device_trust.service import (
    authenticate,
    issue_link,
    list_devices,
    redeem_link,
    revoke_device,
)

__all__ = [
    "authenticate",
    "issue_link",
    "list_devices",
    "redeem_link",
    "revoke_device",
]
