"""Protocols connecting supplier stages to their collaborators."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from content_factory.schemas.supplier.results import FilterDecision
from content_factory.schemas.supplier.video import DownloadedVideo, Video, VideoMetadata


class FoundSink(Protocol):
    """Destination for videos discovered in the search feed."""

    def add_found(self, video: Video) -> bool:
        """Save one found video and return whether it was new."""
        ...


class FilterSink(Protocol):
    """Destination for filter decisions."""

    def save_decision(
        self,
        video: Video,
        metadata: VideoMetadata,
        decision: FilterDecision,
    ) -> None:
        """Save metadata and the approved/rejected decision."""
        ...

    def save_rejection(self, video: Video, reason: str) -> None:
        """Save a rejection that does not need metadata."""
        ...

    def save_failure(self, video: Video, reason: str) -> None:
        """Save a failed metadata probe."""
        ...


class DownloadSink(Protocol):
    """Destination for downloaded videos and download failures."""

    def has_sha256(self, sha256: str) -> bool:
        """Return whether a file with this hash is already stored."""
        ...

    def save_download(self, item: DownloadedVideo) -> None:
        """Persist one downloaded video."""
        ...

    def save_failure(self, video: Video, reason: str) -> None:
        """Persist a failed download attempt."""
        ...

    def save_rejection(self, video: Video, reason: str) -> None:
        """Persist a rejection that does not need a file."""
        ...


ProbeFn = Callable[[str], VideoMetadata]
