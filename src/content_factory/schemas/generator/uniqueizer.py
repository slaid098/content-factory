"""Types of the uniqueizer source queue and of the ready-copy buffer."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SourceCandidate:
    """Metadata of one video file found by the importer.

    The path is relative to the runtime data directory in POSIX form, so the
    queue can resolve it on any host and the database stays portable.
    """

    path: str
    language: str
    profile_id: str
    sha256: str
    size: int
    duration: float | None


@dataclass(frozen=True, slots=True)
class SourceItem:
    """One row of ``source_items`` with its queue state."""

    path: str
    language: str
    profile_id: str
    sha256: str
    size: int
    duration: float | None
    added_at: str
    used_at: str | None
    missing_at: str | None
    use_count: int


class SourceImportAction(StrEnum):
    """What the importer did with one scanned file."""

    ADDED = "added"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    RETURNED = "returned"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Counters of one import pass over a single profile."""

    language: str
    profile_id: str
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    returned: int = 0
    duplicates: int = 0
    missing: int = 0
    skipped: int = 0


@dataclass(frozen=True, slots=True)
class RefillFailure:
    """One source the queue could not turn into a ready copy."""

    source: str
    reason: str


@dataclass(frozen=True, slots=True)
class RefillResult:
    """Outcome of one buffer refill pass."""

    created: int
    ready: int
    exhausted: bool
    failures: tuple[RefillFailure, ...] = ()


class UniqueizerEngine(Protocol):
    """Integration point with the external uniqueizer.

    The factory owns the queue, the buffer and the source metadata, but never
    inspects or changes the produced file. Everything inside
    :meth:`uniqueize` is the engine's responsibility.
    """

    def uniqueize(self, source: Path, target: Path) -> None:
        """Write a unique copy of *source* to *target*.

        Args:
            source: Existing source file from the queue.
            target: Destination path chosen by the queue; the engine creates
                a non-empty file there and does not touch the source.

        Raises:
            Exception: The engine reports a failed copy.
        """
        ...


__all__ = [
    "ImportResult",
    "RefillFailure",
    "RefillResult",
    "SourceCandidate",
    "SourceImportAction",
    "SourceItem",
    "UniqueizerEngine",
]
