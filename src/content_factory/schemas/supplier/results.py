"""Results returned by supplier stages and by one production run."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from content_factory.schemas.supplier.video import DownloadedVideo, Video


class StopReason(StrEnum):
    """Why one production run stopped."""

    ENOUGH_STOCK = "enough_stock"
    TARGET_REACHED = "target_reached"
    QUERIES_EXHAUSTED = "queries_exhausted"
    CAPTCHA = "captcha"
    NO_PROGRESS = "no_progress"
    STOCK_SHORTFALL = "stock_shortfall"
    DURATION_EXCEEDED = "duration_exceeded"
    NO_QUERIES = "no_queries"


@dataclass(frozen=True, slots=True)
class FilterDecision:
    """Result of applying pre-download filters to a video."""

    approved: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class FindResult:
    """Counters returned by one feed-only finder run."""

    found: int = 0
    duplicates: int = 0
    queries_run: int = 0
    links_seen: int = 0
    errors: int = 0
    captcha: bool = False
    blocked_until: str | None = None


@dataclass(frozen=True, slots=True)
class FilterResult:
    """Counters returned by one filter run."""

    approved: tuple[Video, ...] = ()
    rejected: int = 0
    failed: int = 0


@dataclass(frozen=True, slots=True)
class DownloadResult:
    """Counters returned by one downloader run."""

    downloaded: tuple[DownloadedVideo, ...] = ()
    failed: int = 0
    rejected: int = 0


@dataclass(frozen=True, slots=True)
class LanguageResult:
    """Outcome of one language inside a production run."""

    downloaded: int
    stop_reason: StopReason
    blocked_until: str | None = None
    found: int = 0
    approved: int = 0
    rejected: int = 0
    failed: int = 0
    queries_run: int = 0


@dataclass(frozen=True, slots=True)
class ProduceResult:
    """Outcome of one supplier production run over the language plan."""

    produced_by_language: dict[str, int]
    produced_total: int
    stop_reason: StopReason
    blocked_until: str | None = None
