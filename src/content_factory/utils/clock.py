"""UTC timestamps used by the operational journal and its reports."""

from __future__ import annotations

from datetime import UTC, datetime


def now_iso() -> str:
    """Return the current UTC moment as ISO 8601."""
    return datetime.now(UTC).isoformat()


def start_moment(value: str) -> datetime:
    """Parse a stored ISO timestamp, falling back to the current moment."""
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(UTC)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


__all__ = ["now_iso", "start_moment"]
