"""One stock video and the niche that owns it."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StockFile:
    """One mp4 file of the stock and the niche it belongs to."""

    niche: str
    language: str
    path: Path
    profile_id: str | None
