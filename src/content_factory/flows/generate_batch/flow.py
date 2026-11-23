"""Prefect flow for one manual dating video batch."""

from __future__ import annotations

from loguru import logger
from prefect import flow

from content_factory.config.settings import (
    get_settings,
    load_generator_settings,
    prepare_data_dirs,
)
from content_factory.constants import DB_DIR
from content_factory.db.lock import GENERATOR_LOCK, lock_path, try_acquire
from content_factory.exceptions.generator import GeneratorBusyError
from content_factory.services.generator import generate_batch as run_batch


@flow(name="generate_batch")
def generate_batch(
    profiles: str = "dating/profile_2", count: int = 1, dry_run: bool = False
) -> str:
    """Собрать партию дейтинг-роликов и вернуть код исхода.

    Запуск ручной: расписания нет. Файловая блокировка не даёт двум партиям
    одновременно арендовать серверы; второй запуск сразу падает с
    ``GeneratorBusyError``, а не ждёт освобождения.

    Args:
        profiles: Имена профилей через запятую, например
            ``"dating/profile_2,dating/profile_1"``.
        count: Сколько роликов собрать на каждый профиль.
        dry_run: Построить план и записать ``run.json`` без Vast и ComfyUI.
    """
    settings = get_settings()
    prepare_data_dirs(settings)
    lock = try_acquire(lock_path(settings.data_dir / DB_DIR, GENERATOR_LOCK))
    if lock is None:
        raise GeneratorBusyError("генератор уже запущен: дождитесь конца текущей партии")
    try:
        result = run_batch(
            profiles=profiles,
            count=count,
            dry_run=dry_run,
            data_dir=settings.data_dir,
            settings=load_generator_settings(),
        )
    finally:
        lock.release()
    logger.bind(
        flow="generate_batch",
        run_id=result.run_id,
        status=result.status,
        generated=result.generated,
        failed=result.failed,
    ).info("Партия генератора завершена")
    return (
        f"run={result.run_id} status={result.status} "
        f"generated={result.generated} failed={result.failed}"
    )
