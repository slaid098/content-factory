"""Scheduled title generation flow."""

from __future__ import annotations

from loguru import logger
from prefect import flow

from content_factory.config.settings import get_settings, load_titles_settings, prepare_data_dirs
from content_factory.services.titles import assign_titles


@flow(name="titles")
def titles() -> str:
    """Назначить названия стоковым видео и вернуть код исхода."""
    settings = get_settings()
    prepare_data_dirs(settings)
    titles_settings = load_titles_settings()
    if titles_settings.api_key is None:
        logger.bind(flow="titles").error("titles: OPENAI_API_KEY не задан")
        return "disabled"
    result = assign_titles(settings.data_dir, titles_settings)
    logger.bind(
        candidates=result.candidates,
        titled=result.titled,
        skipped=result.skipped,
    ).info("Названия: сохранено {}, пропущено {}", result.titled, result.skipped)
    return f"titled={result.titled} skipped={result.skipped}"
