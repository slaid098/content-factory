"""Service-level exceptions."""

from __future__ import annotations


class ContentFactoryError(Exception):
    """Base exception for every content-factory failure."""


class ConfigError(ContentFactoryError):
    """Raised when configuration cannot be read, created or validated."""
