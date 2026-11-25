"""Service-level exceptions."""

from __future__ import annotations


class ContentFactoryError(Exception):
    """Base exception for every content-factory failure."""


class DatabaseError(Exception):
    """Raised when a database operation fails.

    The database port translates driver errors into this type, so callers do
    not depend on SQLite or PostgreSQL.
    """


class IntegrityError(DatabaseError):
    """Raised when a constraint rejects a write."""


class OperationalError(DatabaseError):
    """Raised when the database is unavailable or the connection fails."""


class LockError(ContentFactoryError):
    """Raised when a cross-process runtime lock cannot be used."""


class LockTimeoutError(LockError):
    """Raised when a cross-process lock is not released within its timeout."""


class LockUnavailableError(LockError):
    """Raised when the platform cannot provide advisory file locks."""


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


class ProfileValidationError(ContentFactoryError):
    """Raised when per-profile refill settings violate a range rule."""


class ProfileStorageError(ContentFactoryError):
    """Raised when per-profile settings cannot be read or written."""


class ProfilePreviewError(ContentFactoryError):
    """Raised when a profile preview frame cannot be extracted."""
