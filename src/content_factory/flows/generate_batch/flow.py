"""Prefect flow for one manual dating video batch."""

from __future__ import annotations

from loguru import logger
from prefect import flow

from content_factory.config.settings import (
    get_settings,
    load_generator_settings,
    prepare_data_dirs,
)
from content_factory.constants import DB_DIR, PROFILES_DIR
from content_factory.db.lock import GENERATOR_LOCK, lock_path, try_acquire
from content_factory.exceptions.generator import GeneratorBusyError, ProfileError
from content_factory.services import production
from content_factory.services.generator import generate_batch as run_batch
from content_factory.services.generator import load_profile, parse_names


@flow(name="generate_batch")
def generate_batch(
    profiles: str = "dating/profile_2", count: int = 1, dry_run: bool = False
) -> str:
    """Собрать партию дейтинг-роликов и вернуть код исхода.

    Запуск ручной: расписания нет. Файловая блокировка не даёт двум партиям
    одновременно арендовать серверы; второй запуск сразу падает с
    ``GeneratorBusyError``, а не ждёт освобождения. Producer каждого шага
    берётся из рецепта ``production`` профиля; при ``dry_run`` producer не
    вызывается, но рецепт проверяется и попадает в ``run.json``.

    Args:
        profiles: Имена профилей через запятую, например
            ``"dating/profile_2,dating/profile_1"``.
        count: Сколько роликов собрать на каждый профиль.
        dry_run: Построить план и записать ``run.json`` без Vast и ComfyUI.

    Raises:
        ProfileError: Рецепт профиля пуст или называет неизвестный producer.
    """
    settings = get_settings()
    prepare_data_dirs(settings)
    lock = try_acquire(lock_path(settings.data_dir / DB_DIR, GENERATOR_LOCK))
    if lock is None:
        raise GeneratorBusyError("генератор уже запущен: дождитесь конца текущей партии")
    try:
        generator_settings = load_generator_settings()
        loaded = [
            load_profile(name, settings.data_dir / PROFILES_DIR) for name in parse_names(profiles)
        ]
        if dry_run:
            planned = run_batch(
                profiles=profiles,
                count=count,
                dry_run=True,
                data_dir=settings.data_dir,
                settings=generator_settings,
            )
            run_id, status = planned.run_id, planned.status
            generated, failed = planned.generated, planned.failed
        else:
            batches = [
                production.resolve_producer(step.producer).produce(
                    profile, count, settings.data_dir, generator_settings
                )
                for profile in loaded
                for step in profile.production
            ]
            if not batches:
                raise ProfileError("рецепт production не дал ни одного шага")
            run_id = batches[0].run_id
            generated = sum(batch.generated for batch in batches)
            failed = sum(batch.failed for batch in batches)
            status = "failed" if failed else "done"
    finally:
        lock.release()
    logger.bind(
        flow="generate_batch",
        run_id=run_id,
        status=status,
        generated=generated,
        failed=failed,
    ).info("Партия генератора завершена")
    return f"run={run_id} status={status} generated={generated} failed={failed}"
