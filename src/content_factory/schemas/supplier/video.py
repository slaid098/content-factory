"""Video types shared by the supplier stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_DOWNLOADED = "downloaded"
STATUS_FAILED = "failed"
STATUS_FOUND = "found"
VIDEO_STATUSES = (
    STATUS_FOUND,
    STATUS_APPROVED,
    STATUS_REJECTED,
    STATUS_DOWNLOADED,
    STATUS_FAILED,
)


def utcnow() -> str:
    """Return the current UTC timestamp in ISO-8601 form."""
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class VideoMetadata:
    """Metadata parsed from a TikTok video page."""

    video_id: str
    url: str
    author_id: str
    author_name: str
    description: str
    views: int | None
    followers: int | None
    duration: int | None
    is_aigc: bool
    aigc_description: str
    width: int | None
    height: int | None
    create_time: int | None = None


@dataclass(frozen=True, slots=True)
class Video:
    """One TikTok video discovered in the search feed."""

    video_id: str
    url: str
    author: str
    query: str = ""
    language: str = ""
    discovered_at: str = field(default_factory=utcnow)


@dataclass(frozen=True, slots=True)
class DownloadedVideo:
    """One downloaded video file and its content hash."""

    video: Video
    path: str
    sha256: str
    size: int
