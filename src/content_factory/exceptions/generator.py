"""Errors of the dating video generator capsule."""

from __future__ import annotations

from content_factory.exceptions.errors import ContentFactoryError


class GeneratorError(ContentFactoryError):
    """Raised when a generator batch cannot build, run or finish."""


class GeneratorBusyError(GeneratorError):
    """Raised when another generator batch already holds the run lock."""


class ProfileError(GeneratorError):
    """Raised when a generator profile is missing or malformed."""


class RentError(GeneratorError):
    """Raised when a Vast.ai server cannot be rented, prepared or destroyed."""


class JournalError(GeneratorError):
    """Raised when the rented-server journal cannot be read or written."""


class ComfyError(GeneratorError):
    """Raised when ComfyUI rejects a workflow or stops returning results."""


class SafetyError(GeneratorError):
    """Raised when Clef cannot check the frames of a generated source."""


class TextError(GeneratorError):
    """Raised when the text model is unavailable or returns an unusable answer."""


class OverlayError(GeneratorError):
    """Raised when the text card or the music cannot be mixed into the video."""


class QaError(GeneratorError):
    """Raised when a produced video fails the mp4 check."""
