"""Mutable limits and counters accumulated during one production run."""

from __future__ import annotations

from dataclasses import dataclass

from content_factory.schemas.supplier.results import (
    DownloadResult,
    FilterResult,
    FindResult,
    LanguageResult,
    StopReason,
)


@dataclass(slots=True)
class Budget:
    """Run-wide search and filter limits shared by all languages."""

    queries: int
    filters: int


@dataclass(slots=True)
class QueryStats:
    """Counters collected while crawling one search query."""

    links_seen: int = 0
    found: int = 0
    duplicates: int = 0
    errors: int = 0


@dataclass(slots=True)
class FindTotals:
    """Counters aggregated over one finder batch."""

    found: int = 0
    duplicates: int = 0
    queries_run: int = 0
    links_seen: int = 0
    errors: int = 0
    captcha: bool = False
    blocked_until: str | None = None

    def absorb(self, query: QueryStats) -> None:
        """Add one query result to the batch totals."""
        self.found += query.found
        self.duplicates += query.duplicates
        self.queries_run += 1
        self.links_seen += query.links_seen
        self.errors += query.errors


@dataclass(slots=True)
class LanguageCounters:
    """Counters of one language that are reported in its result."""

    found: int = 0
    approved: int = 0
    rejected: int = 0
    failed: int = 0
    downloaded: int = 0
    queries_run: int = 0
    attempts: int = 0

    def absorb_find(self, result: FindResult) -> None:
        """Add search counters."""
        self.found += result.found
        self.queries_run += result.queries_run

    def absorb_filter(self, result: FilterResult) -> None:
        """Add filter counters."""
        self.approved += len(result.approved)
        self.rejected += result.rejected
        self.failed += result.failed

    def absorb_download(self, result: DownloadResult) -> None:
        """Add download counters."""
        self.downloaded += len(result.downloaded)
        self.failed += result.failed
        self.attempts += len(result.downloaded) + result.failed

    def result(
        self,
        reason: StopReason,
        blocked_until: str | None = None,
    ) -> LanguageResult:
        """Build the language result."""
        return LanguageResult(
            downloaded=self.downloaded,
            stop_reason=reason,
            blocked_until=blocked_until,
            found=self.found,
            approved=self.approved,
            rejected=self.rejected,
            failed=self.failed,
            queries_run=self.queries_run,
        )
