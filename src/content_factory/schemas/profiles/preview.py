"""Preview file of one profile and the media type it is served with."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ProfilePreview:
    """One preview file and the media type it is served with."""

    path: Path
    media_type: str


__all__ = ["ProfilePreview"]
