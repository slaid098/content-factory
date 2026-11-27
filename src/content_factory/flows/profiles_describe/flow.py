"""Scheduled flow that describes profiles for the operator."""

from __future__ import annotations

from loguru import logger
from prefect import flow

from content_factory.config.settings import (
    get_settings,
    load_generator_settings,
    prepare_data_dirs,
)
from content_factory.exceptions.errors import ProfileDescriptionError
from content_factory.services.generator import TextClient
from content_factory.services.profiles import describe_profile, list_profiles


@flow(name="profiles_describe")
def profiles_describe(
    *,
    writer: TextClient | None = None,
    force: bool = False,
) -> str:
    """Заполнить title и summary профилей и вернуть счётчики.

    Без ``force`` уже описанные профили (оба поля непустые) пропускаются без
    вызова модели. С ``force`` каждый профиль описывается заново. Ошибка
    одного профиля попадает в ``failed`` и не останавливает остальные. Без
    ``OPENAI_API_KEY`` flow выходит со статусом ``disabled``.

    Args:
        writer: Порт текстовой модели; тесты подставляют свой, по умолчанию
            используется :class:`TextClient` с настройками генератора.
        force: Перегенерировать описания даже у заполненных профилей.
    """
    settings = get_settings()
    prepare_data_dirs(settings)
    generator_settings = load_generator_settings()
    if generator_settings.api_key is None:
        logger.bind(flow="profiles_describe").error("profiles_describe: OPENAI_API_KEY не задан")
        return "disabled"
    model = writer or TextClient(generator_settings)
    described = skipped = failed = 0
    for profile in list_profiles(settings.data_dir):
        if not force and profile.title and profile.summary:
            skipped += 1
            continue
        try:
            describe_profile(
                settings.data_dir,
                profile.id,
                generator_settings,
                writer=model,
                force=force,
            )
        except ProfileDescriptionError as ex:
            failed += 1
            logger.bind(
                flow="profiles_describe",
                profile_id=profile.id,
                error_type=type(ex).__name__,
            ).error("Профиль {} не описан: {}", profile.id, ex)
        else:
            described += 1
    outcome = f"described={described} skipped={skipped} failed={failed}"
    logger.bind(
        flow="profiles_describe",
        described=described,
        skipped=skipped,
        failed=failed,
    ).info("Описание профилей: {}", outcome)
    return outcome


__all__ = ["profiles_describe"]
