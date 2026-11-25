"""One stock media file and the niche that owns it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from content_factory.schemas.content.delivery import ContentType


@dataclass(frozen=True, slots=True)
class StockFile:
    """One stock media file and the niche it belongs to."""

    niche: str
    language: str
    path: Path
    profile_id: str | None
    content_type: ContentType = ContentType.VIDEO
