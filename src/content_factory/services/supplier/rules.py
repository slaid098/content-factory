"""Фильтры контента до скачивания."""

from __future__ import annotations

import time

from content_factory.schemas.supplier.results import FilterDecision
from content_factory.schemas.supplier.settings import SupplierConfig
from content_factory.schemas.supplier.video import VideoMetadata


def evaluate(metadata: VideoMetadata, cfg: SupplierConfig) -> FilterDecision:
    """Применить фильтры поставщика к одному видео.

    Args:
        metadata: Метаданные страницы TikTok.
        cfg: Настройки с пределами просмотров, подписчиков и возраста.

    Returns:
        Решение с первой причиной отказа либо одобрение.
    """
    if metadata.is_aigc or metadata.aigc_description:
        return FilterDecision(False, "aigc")
    if metadata.duration is None or metadata.duration <= 0:
        return FilterDecision(False, "invalid_duration")
    if metadata.duration < cfg.min_duration or metadata.duration > cfg.max_duration:
        return FilterDecision(False, "duration_out_of_range")
    if (
        metadata.width is None
        or metadata.height is None
        or metadata.width <= 0
        or metadata.height <= 0
    ):
        return FilterDecision(False, "unknown_dimensions")
    if metadata.height <= metadata.width:
        return FilterDecision(False, "not_vertical")
    if metadata.views is None:
        return FilterDecision(False, "unknown_views")
    if metadata.views > cfg.max_views:
        return FilterDecision(False, "too_many_views")
    if metadata.followers is None:
        return FilterDecision(False, "unknown_followers")
    if metadata.followers > cfg.max_followers:
        return FilterDecision(False, "too_many_followers")
    if cfg.min_age_days > 0:
        now = time.time()
        if metadata.create_time is None or metadata.create_time <= 0 or metadata.create_time > now:
            return FilterDecision(False, "unknown_age")
        if now - metadata.create_time < cfg.min_age_days * 86400:
            return FilterDecision(False, "too_young")
    return FilterDecision(True, None)
