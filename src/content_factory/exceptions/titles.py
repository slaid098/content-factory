"""Title generation exceptions."""

from __future__ import annotations


class TitleError(Exception):
    """Raised when a title cannot be generated for one video."""


class TitleClientError(TitleError):
    """Raised when the title API rejects a request or returns bad data."""


class TransientTitleError(TitleError):
    """Raised when the title API is temporarily unavailable."""
