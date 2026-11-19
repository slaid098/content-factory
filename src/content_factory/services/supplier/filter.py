"""Фильтр метаданных для видео, найденных в ленте TikTok."""

from __future__ import annotations

import time
from collections.abc import Collection, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import cast

from loguru import logger

from content_factory.exceptions.errors import SupplierError
from content_factory.schemas.supplier.ports import FilterSink, ProbeFn
from content_factory.schemas.supplier.results import FilterResult
from content_factory.schemas.supplier.settings import SupplierConfig
from content_factory.schemas.supplier.video import STATUS_FOUND, Video, VideoMetadata
from content_factory.services.supplier.metadata import fetch_video_metadata
from content_factory.services.supplier.proxy import resolve_proxy
from content_factory.services.supplier.repository import Repository
from content_factory.services.supplier.rules import evaluate

_PROBE_ATTEMPTS = 2


def _default_probe(cfg: SupplierConfig) -> ProbeFn:
    proxy = resolve_proxy(cfg)

    def probe(url: str) -> VideoMetadata:
        return fetch_video_metadata(url, proxy=proxy, timeout=30)

    return probe


def _probe_video(video: Video, probe: ProbeFn, pause: float) -> VideoMetadata | None:
    last_error: SupplierError | None = None
    for _ in range(_PROBE_ATTEMPTS):
        try:
            metadata = probe(video.url)
        except SupplierError as ex:
            last_error = ex
            continue
        if pause > 0:
            time.sleep(pause)
        return metadata

    if pause > 0:
        time.sleep(pause)
    error = cast(SupplierError, last_error)
    logger.bind(error=error, error_type=type(error).__name__).warning(
        "Не удалось получить метаданные {}", video.video_id
    )
    return None


def _process_author(
    cfg: SupplierConfig,
    videos: Sequence[Video],
    sink: FilterSink,
    probe: ProbeFn,
) -> tuple[list[Video], int, int]:
    approved: list[Video] = []
    rejected = 0
    failed = 0

    for video in videos:
        if len(approved) >= cfg.max_videos_per_author:
            break
        metadata = _probe_video(video, probe, cfg.filter_pause)
        if metadata is None:
            sink.save_failure(video, "metadata_error")
            failed += 1
            continue

        decision = evaluate(metadata, cfg)
        sink.save_decision(video, metadata, decision)
        if decision.approved:
            approved.append(video)
        else:
            rejected += 1
            logger.bind(video_id=video.video_id, reason=decision.reason).info(
                "Видео {} отклонено: {}", video.video_id, decision.reason
            )

    return approved, rejected, failed


def filter_videos(
    cfg: SupplierConfig,
    videos: Sequence[Video],
    known_ids: Collection[str],
    used_authors: Collection[str],
    sink: FilterSink,
    probe: ProbeFn | None = None,
) -> FilterResult:
    """Проверить найденные видео по метаданным и занятым авторам.

    Args:
        cfg: Настройки с лимитами фильтров и числом потоков.
        videos: Найденные видео для проверки.
        known_ids: Идентификаторы видео с финальным статусом.
        used_authors: Авторы, у которых уже есть скачанное видео.
        sink: Получатель каждого решения.
        probe: Необязательный загрузчик метаданных; по умолчанию HTTP.

    Returns:
        Одобренные видео и счётчики отказов и сбоев.
    """
    if not videos:
        return FilterResult()

    known = set(known_ids)
    used = set(used_authors)
    groups: dict[str, list[Video]] = {}
    order: dict[str, int] = {}
    seen: set[str] = set()
    rejected = 0

    for video in videos:
        if video.video_id in seen:
            continue
        seen.add(video.video_id)
        order[video.video_id] = len(order)
        if video.video_id in known:
            sink.save_rejection(video, "already_known")
            rejected += 1
            continue
        if video.author in used:
            sink.save_rejection(video, "author_used")
            rejected += 1
            continue
        groups.setdefault(video.author, []).append(video)

    if not groups:
        return FilterResult(rejected=rejected)

    probe_fn = probe or _default_probe(cfg)
    approved: list[Video] = []
    failed = 0

    with ThreadPoolExecutor(max_workers=cfg.probe_workers) as pool:
        results = pool.map(
            lambda items: _process_author(cfg, items, sink, probe_fn),
            groups.values(),
        )
        for author_approved, author_rejected, author_failed in results:
            approved.extend(author_approved)
            rejected += author_rejected
            failed += author_failed

    approved.sort(key=lambda video: order[video.video_id])
    return FilterResult(approved=tuple(approved), rejected=rejected, failed=failed)


class Filter:
    """Этап фильтра: проверка найденных видео одного языка."""

    def __init__(
        self,
        cfg: SupplierConfig,
        repo: Repository,
        probe: ProbeFn | None = None,
    ) -> None:
        """Создать этап фильтра.

        Args:
            cfg: Настройки поставщика.
            repo: Репозиторий найденных видео.
            probe: Необязательный загрузчик метаданных, в основном для тестов.
        """
        self._cfg = cfg
        self._repo = repo
        self._probe = probe

    def filter(self, limit: int) -> FilterResult:
        """Проверить не более ``limit`` найденных видео языка.

        Args:
            limit: Максимум видео, которые можно проверить за прогон.

        Returns:
            Одобренные видео и счётчики отказов и сбоев.
        """
        if limit <= 0:
            return FilterResult()
        videos = self._repo.list_videos(STATUS_FOUND, language=self._cfg.language, limit=limit)
        if not videos:
            return FilterResult()
        return filter_videos(
            self._cfg,
            videos,
            self._repo.decided_video_ids(),
            self._repo.used_authors(),
            self._repo,
            probe=self._probe,
        )
