"""Prefect flow for one manual dating video batch."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

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
from content_factory.schemas.content.delivery import ContentType
from content_factory.schemas.generator.run import ProductionStep
from content_factory.services import production
from content_factory.services.generator import generate_batch as run_batch
from content_factory.services.generator import load_profile, parse_names, validate_count

if TYPE_CHECKING:
    from content_factory.config.settings import GeneratorSettings


def _is_default_recipe(steps: tuple[ProductionStep, ...]) -> bool:
    """Return True when the recipe is the single default Vast video step."""
    return (
        len(steps) == 1
        and steps[0].producer == production.VAST_PRODUCER
        and steps[0].type is ContentType.VIDEO
    )


def _produce(
    profile: production.ProfileRef,
    step: ProductionStep,
    count: int,
    data_dir: Path,
    settings: GeneratorSettings,
) -> production.ProducedBatch:
    """Run one recipe step through the producer registered under its name."""
    return production.resolve_producer(step.producer).produce(profile, count, data_dir, settings)


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

    Если у всех профилей рецепт — один шаг ``vast/video``, flow вызывает
    ``run_batch`` один раз со всеми профилями: он проверяет ``count`` и планы
    всех профилей до аренды и даёт общий ``run_id``. Для неоднородных
    рецептов flow проверяет только ``count``, а план каждого шага проверяет
    его producer перед своей арендой; каждый шаг даёт свой ``run_id``, и все
    они возвращаются в строке исхода.

    Args:
        profiles: Имена профилей через запятую, например
            ``"dating/profile_2,dating/profile_1"``.
        count: Сколько роликов собрать на каждый профиль.
        dry_run: Построить план и записать ``run.json`` без Vast и ComfyUI.

    Raises:
        GeneratorError: ``count`` не положительное целое.
        ProfileError: Рецепт профиля пуст или называет неизвестный producer.
    """
    settings = get_settings()
    prepare_data_dirs(settings)
    validate_count(count)
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
            run_ids = [planned.run_id]
            status, generated, failed = planned.status, planned.generated, planned.failed
        elif all(_is_default_recipe(profile.production) for profile in loaded):
            batch = run_batch(
                profiles=profiles,
                count=count,
                data_dir=settings.data_dir,
                settings=generator_settings,
            )
            run_ids = [batch.run_id]
            status, generated, failed = batch.status, batch.generated, batch.failed
        else:
            batches = [
                _produce(profile, step, count, settings.data_dir, generator_settings)
                for profile in loaded
                for step in profile.production
            ]
            if not batches:
                raise ProfileError("рецепт production не дал ни одного шага")
            run_ids = [batch.run_id for batch in batches]
            generated = sum(batch.generated for batch in batches)
            failed = sum(batch.failed for batch in batches)
            status = "failed" if failed else "done"
    finally:
        lock.release()
    logger.bind(
        flow="generate_batch",
        run_id=run_ids[0] if len(run_ids) == 1 else run_ids,
        status=status,
        generated=generated,
        failed=failed,
    ).info("Партия генератора завершена")
    runs = " ".join(f"run={run_id}" for run_id in run_ids)
    return f"{runs} status={status} generated={generated} failed={failed}"
