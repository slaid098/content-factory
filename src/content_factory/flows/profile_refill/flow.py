"""Scheduled refill of enabled generator profiles."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from loguru import logger
from prefect import flow
from prefect.deployments import run_deployment

from content_factory.config.runtime import load_runtime_config
from content_factory.config.settings import get_settings, prepare_data_dirs
from content_factory.constants import CONFIG_DIR, CONFIG_FILENAME, DB_DIR, DB_FILENAME
from content_factory.db.connection import connect
from content_factory.db.lock import GENERATOR_LOCK, lock_path, try_acquire
from content_factory.db.profiles import get_refill
from content_factory.db.stock import count_by_profile
from content_factory.schemas.content.delivery import ContentType
from content_factory.schemas.profiles.refill import RefillDecision
from content_factory.services.profiles import decide_refill, list_profiles
from content_factory.services.stock import sync_stock


@flow(name="profile_refill")
def profile_refill(starter: Callable[[str, int, ContentType], None] | None = None) -> str:
    """Выполнить один тик добора профилей и вернуть код исхода.

    Включённые профили проверяются по остатку видео и фото: первый по id
    профиль, у которого остаток не выше порога, запускает ``generate_batch``
    на нехватку до цели ровно для одного типа контента. Видео проверяется
    раньше фото, поэтому профиль с нехваткой обоих типов получает видео
    в этом тике, а фото — в следующем. По умолчанию все профили выключены, и
    тик ничего не делает.

    Args:
        starter: Запуск партии ``starter(profile_id, count, content_type)``;
            по умолчанию создаёт flow run ``generate_batch`` и не ждёт его
            конца.
    """
    settings = get_settings()
    prepare_data_dirs(settings)
    runtime = load_runtime_config(settings.data_dir / CONFIG_DIR / CONFIG_FILENAME)
    profiles = list_profiles(settings.data_dir)
    connection = connect(settings.data_dir / DB_DIR / DB_FILENAME)
    try:
        refills = {profile.id: get_refill(connection, profile.id) for profile in profiles}
        if not any(refill.enabled for refill in refills.values()):
            logger.bind().info("Добор профилей выключен: нет включённых профилей; исход disabled")
            return "disabled"
        sync_stock(settings.data_dir)
        stock = {profile.id: count_by_profile(connection, profile.id) for profile in profiles}
        photo_stock = {
            profile.id: count_by_profile(connection, profile.id, content_type=ContentType.PHOTO)
            for profile in profiles
        }
    finally:
        connection.close()

    decision = decide_refill(profiles, refills, stock, photo_stock, runtime.generator)
    if decision is None:
        logger.bind().info(
            "Добор профилей не нужен: у включённых профилей хватает стока; исход idle"
        )
        return "idle"
    return _start_refill(settings.data_dir, decision, starter)


def _start_refill(
    data_dir: Path,
    decision: RefillDecision,
    starter: Callable[[str, int, ContentType], None] | None,
) -> str:
    """Start the chosen refill unless the generator lock is taken."""
    lock = try_acquire(lock_path(data_dir / DB_DIR, GENERATOR_LOCK))
    if lock is None:
        logger.bind().info(
            "Генератор занят: добор профиля {} ({}) отложен, остаток {}, цель {}, порог {}, "
            "нужно {}; исход generator_busy",
            decision.profile_id,
            decision.content_type.value,
            decision.target - decision.count,
            decision.target,
            decision.refill_at,
            decision.count,
        )
        return "generator_busy"
    lock.release()
    start = starter or _start_deployment
    try:
        start(decision.profile_id, decision.count, decision.content_type)
    except Exception as ex:
        logger.bind().error(
            "Не удалось запустить добор профиля {} ({}): {}: {}; исход start_failed",
            decision.profile_id,
            decision.content_type.value,
            type(ex).__name__,
            ex,
        )
        return "start_failed"
    logger.bind().info(
        "Запущен добор профиля {} ({}): остаток {}, цель {}, порог {}, нужно {}; исход started",
        decision.profile_id,
        decision.content_type.value,
        decision.target - decision.count,
        decision.target,
        decision.refill_at,
        decision.count,
    )
    return f"started:{decision.profile_id}"


def _start_deployment(
    profile_id: str,
    count: int,
    content_type: ContentType,
) -> None:
    """Create a ``generate_batch`` flow run without waiting for its end."""
    run_deployment(
        "generate_batch/generate_batch",
        parameters={
            "profiles": profile_id,
            "count": count,
            "content_type": content_type.value,
        },
        timeout=0,
    )


__all__ = ["profile_refill"]
