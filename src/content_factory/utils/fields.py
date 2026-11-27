"""Reading optional scalar fields out of untyped metadata."""

from __future__ import annotations


def optional_text(value: object) -> str | None:
    """Return *value* as a non-empty string, or ``None``."""
    if value is None:
        return None
    text = str(value)
    return text or None


def optional_int(value: object) -> int | None:
    """Return *value* when it is an integer, not a boolean, or ``None``."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def positive_int(value: object) -> int | None:
    """Return *value* when it is a positive integer, or ``None``."""
    number = optional_int(value)
    return number if number is not None and number > 0 else None


__all__ = ["optional_int", "optional_text", "positive_int"]
