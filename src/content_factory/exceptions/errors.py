"""Service-level exceptions."""

from __future__ import annotations


class ContentFactoryError(Exception):
    """Base exception for every content-factory failure."""


class ConfigError(ContentFactoryError):
    """Raised when configuration cannot be read, created or validated."""


class SupplierError(ContentFactoryError):
    """Raised when the TikTok supplier engine cannot complete a stage."""


class DeliveryError(ContentFactoryError):
    """Raised when a content delivery archive cannot be built."""


class DeliveryBusyError(DeliveryError):
    """Raised when another delivery archive is already being built."""


class UploadError(DeliveryError):
    """Raised when a built archive cannot be published to Litterbox."""


class ArchiveTooLargeError(UploadError):
    """Raised when an archive exceeds the Litterbox upload limit."""
