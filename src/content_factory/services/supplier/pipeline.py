"""Проход по одному языку: остатки, поиск, фильтр и скачивание."""

from __future__ import annotations

import time
from collections.abc import Sequence

from loguru import logger

from content_factory.schemas.supplier.progress import Budget, LanguageCounters
from content_factory.schemas.supplier.results import FindResult, LanguageResult, StopReason
from content_factory.schemas.supplier.search import QuerySpec
from content_factory.schemas.supplier.settings import SupplierConfig
from content_factory.schemas.supplier.video import STATUS_APPROVED, STATUS_FOUND
from content_factory.services.supplier.downloader import Downloader
from content_factory.services.supplier.filter import Filter
from content_factory.services.supplier.finder import Finder
from content_factory.services.supplier.mcp import McpClient
from content_factory.services.supplier.planning import plan_candidates
from content_factory.services.supplier.repository import Repository


def _expired(deadline: float) -> bool:
    """Return whether the run deadline has passed."""
    return time.monotonic() >= deadline


def _filter_pending(
    cfg: SupplierConfig,
    repo: Repository,
    filter_stage: Filter,
    budget: Budget,
    deadline: float,
    counters: LanguageCounters,
) -> StopReason | None:
    """Filter found videos while the run budget and deadline allow it."""
    if budget.filters <= 0:
        return None
    pending = repo.list_videos(
        STATUS_FOUND,
        language=cfg.language,
        limit=budget.filters,
    )
    if not pending:
        return None
    if _expired(deadline):
        return StopReason.DURATION_EXCEEDED
    filter_result = filter_stage.filter(limit=budget.filters)
    counters.absorb_filter(filter_result)
    budget.filters -= len(pending)
    return None


def _download_approved(
    cfg: SupplierConfig,
    repo: Repository,
    downloader: Downloader,
    target: int,
    need: int,
    deadline: float,
    counters: LanguageCounters,
) -> tuple[int, StopReason | None]:
    """Download approved videos and return the refreshed deficit."""
    if not repo.list_videos(STATUS_APPROVED, language=cfg.language, limit=1):
        return need, None
    if _expired(deadline):
        return need, StopReason.DURATION_EXCEEDED
    download_result = downloader.download(limit=need)
    counters.absorb_download(download_result)
    return max(0, target - repo.downloaded_count(cfg.language)), None


def _exhausted_reason(counters: LanguageCounters, no_queries: bool) -> StopReason:
    """Choose the stop reason when there is nothing left to search."""
    if no_queries:
        return StopReason.NO_QUERIES
    if counters.attempts > 0 and counters.downloaded == 0:
        return StopReason.NO_PROGRESS
    return StopReason.QUERIES_EXHAUSTED


def _search(
    finder: Finder,
    queries: Sequence[QuerySpec],
    index: int,
    need: int,
    budget: Budget,
    counters: LanguageCounters,
) -> tuple[int, FindResult]:
    """Run one finder batch and advance the query position."""
    batch = list(queries[index : index + budget.queries])
    limit = plan_candidates(need, budget.filters)
    find_result = finder.find(batch, limit)
    counters.absorb_find(find_result)
    budget.queries -= find_result.queries_run
    return index + find_result.queries_run, find_result


def run_language(
    cfg: SupplierConfig,
    *,
    repo: Repository,
    mcp: McpClient,
    queries: Sequence[QuerySpec],
    target: int,
    budget: Budget,
    deadline: float,
    no_queries: bool = False,
) -> LanguageResult:
    """Выполнить производство одного языка до цели или исчерпания ресурсов.

    Сначала дорабатываются найденные и одобренные видео прошлых запусков,
    затем ищутся новые. Капча и истёкший дедлайн немедленно прекращают язык.

    Args:
        cfg: Настройки языка, включая каталог контента.
        repo: Репозиторий поставщика.
        mcp: Открытый клиент BitBrowser MCP.
        queries: Запросы языка в порядке выполнения.
        target: Цель языка: уже скачанное плюс доля плана.
        budget: Общие лимиты запуска, уменьшаются на использованное.
        deadline: Момент ``time.monotonic()``, после которого работать нельзя.
        no_queries: У языка нет ни одного запроса; после добора остатков
            причина остановки — ``NO_QUERIES``.

    Returns:
        Итог языка и причина остановки.
    """
    need = target - repo.downloaded_count(cfg.language)
    if need <= 0:
        return LanguageResult(downloaded=0, stop_reason=StopReason.TARGET_REACHED)

    finder = Finder(cfg, repo, mcp)
    filter_stage = Filter(cfg, repo)
    downloader = Downloader(cfg, repo)
    counters = LanguageCounters()
    index = 0

    while True:
        if _expired(deadline):
            return counters.result(StopReason.DURATION_EXCEEDED)

        stopped = _filter_pending(cfg, repo, filter_stage, budget, deadline, counters)
        if stopped is not None:
            return counters.result(stopped)

        need, stopped = _download_approved(
            cfg,
            repo,
            downloader,
            target,
            need,
            deadline,
            counters,
        )
        if stopped is not None:
            return counters.result(stopped)

        if need <= 0:
            return counters.result(StopReason.TARGET_REACHED)
        if index >= len(queries) or budget.queries <= 0:
            return counters.result(_exhausted_reason(counters, no_queries))
        if budget.filters <= 0:
            return counters.result(StopReason.STOCK_SHORTFALL)

        if _expired(deadline):
            return counters.result(StopReason.DURATION_EXCEEDED)
        index, find_result = _search(finder, queries, index, need, budget, counters)

        if find_result.captcha:
            return counters.result(StopReason.CAPTCHA, find_result.blocked_until)
        if find_result.queries_run == 0 and find_result.found == 0:
            return counters.result(StopReason.NO_PROGRESS)
        logger.bind(language=cfg.language).info(
            "Язык {}: нужно {}, найдено {}, запросов {}",
            cfg.language,
            need,
            find_result.found,
            find_result.queries_run,
        )
