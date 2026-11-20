"""Service-level exceptions."""

from __future__ import annotations


class ContentFactoryError(Exception):
    """Base exception for every content-factory failure."""


class ConfigError(ContentFactoryError):
    """Raised when configuration cannot be read, created or validated."""


class SettingsValidationError(ContentFactoryError):
    """Raised when edited runtime settings do not pass validation."""


class SettingsWriteError(ContentFactoryError):
    """Raised when the runtime settings file cannot be written safely."""


class SupplierError(ContentFactoryError):
    """Raised when the TikTok supplier engine cannot complete a stage."""


class DeliveryError(ContentFactoryError):
    """Raised when a content delivery archive cannot be built."""


class DeliveryBusyError(DeliveryError):
    """Raised when another delivery archive is already being built."""


class UploadError(DeliveryError):
    """Raised when a built archive cannot be published to Filebin."""


class ArchiveTooLargeError(UploadError):
    """Raised when an archive exceeds the 1 GiB upload limit."""
