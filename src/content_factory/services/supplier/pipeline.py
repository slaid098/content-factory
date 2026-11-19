"""Проход по одному языку: остатки, поиск, фильтр и скачивание."""

from __future__ import annotations

import time
from collections.abc import Sequence

from loguru import logger

from content_factory.schemas.supplier.progress import Budget, LanguageCounters
from content_factory.schemas.supplier.results import LanguageResult, StopReason
from content_factory.schemas.supplier.search import QuerySpec
from content_factory.schemas.supplier.settings import SupplierConfig
from content_factory.schemas.supplier.video import STATUS_APPROVED, STATUS_FOUND
from content_factory.services.supplier.downloader import Downloader
from content_factory.services.supplier.filter import Filter
from content_factory.services.supplier.finder import Finder
from content_factory.services.supplier.mcp import McpClient
from content_factory.services.supplier.planning import plan_candidates
from content_factory.services.supplier.repository import Repository


def run_language(
    cfg: SupplierConfig,
    *,
    repo: Repository,
    mcp: McpClient,
    queries: Sequence[QuerySpec],
    target: int,
    budget: Budget,
    deadline: float,
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
        if time.monotonic() >= deadline:
            return counters.result(StopReason.DURATION_EXCEEDED)

        if budget.filters > 0:
            pending = repo.list_videos(
                STATUS_FOUND,
                language=cfg.language,
                limit=budget.filters,
            )
            if pending:
                filter_result = filter_stage.filter(limit=budget.filters)
                counters.absorb_filter(filter_result)
                budget.filters -= len(pending)

        if repo.list_videos(STATUS_APPROVED, language=cfg.language, limit=1):
            download_result = downloader.download(limit=need)
            counters.absorb_download(download_result)
            need = max(0, target - repo.downloaded_count(cfg.language))

        if need <= 0:
            return counters.result(StopReason.TARGET_REACHED)
        if budget.filters <= 0:
            return counters.result(StopReason.STOCK_SHORTFALL)
        if index >= len(queries) or budget.queries <= 0:
            if counters.attempts > 0 and counters.downloaded == 0:
                return counters.result(StopReason.NO_PROGRESS)
            return counters.result(StopReason.QUERIES_EXHAUSTED)

        batch = list(queries[index : index + budget.queries])
        limit = plan_candidates(need, budget.filters)
        find_result = finder.find(batch, limit)
        counters.absorb_find(find_result)
        budget.queries -= find_result.queries_run
        index += find_result.queries_run

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
