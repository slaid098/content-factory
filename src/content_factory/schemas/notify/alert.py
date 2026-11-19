"""Critical events that may be reported to the operations chat."""

from __future__ import annotations

from enum import StrEnum


class AlertKind(StrEnum):
    """Kinds of critical alerts; each kind has its own cooldown."""

    CAPTCHA_STREAK = "captcha_streak"
    ZERO_PRODUCTION = "zero_production"
    SUPPLIER_CRASH = "supplier_crash"
    NO_QUERIES = "no_queries"
    BITBROWSER_UNAVAILABLE = "bitbrowser_unavailable"
    STORAGE_ERROR = "storage_error"
    ALL_DOWNLOADS_FAILED = "all_downloads_failed"
