"""Source pool records and the outcome of one import upsert."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SourceImportAction(StrEnum):
    """What an import upsert did with one file."""

    ADDED = "added"
    UPDATED = "updated"
    RESTORED = "restored"
    UNCHANGED = "unchanged"
    DUPLICATE = "duplicate"


@dataclass(slots=True)
class ProfileImportStats:
    """Import counters of one language and profile."""

    added: int = 0
    updated: int = 0
    restored: int = 0
    unchanged: int = 0
    duplicates: int = 0
    missing: int = 0


@dataclass(slots=True)
class ImportStats:
    """Counters of one import run."""

    profiles: dict[str, ProfileImportStats] = field(default_factory=dict)
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class SourceItem:
    """One imported source file of a profile."""

    id: int
    language: str
    profile_id: str
    path: str
    sha256: str
    size: int
    duration: float
    added_at: str
    used_at: str | None = None
    use_count: int = 0
    missing_at: str | None = None


__all__ = [
    "ImportStats",
    "ProfileImportStats",
    "SourceImportAction",
    "SourceItem",
]
