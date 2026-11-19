"""Search query and feed-card types used by the finder."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class QuerySpec:
    """One search query and the language file it came from."""

    query: str
    language: str
    source: Path


@dataclass(frozen=True, slots=True)
class SearchCard:
    """One video card parsed from the search results DOM."""

    href_prefix: str
    ref: str
    is_video: bool
