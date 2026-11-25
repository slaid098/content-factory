"""Prefect flow for one manual dating content batch."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
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
from content_factory.schemas.generator.pipeline import Layer, PipelineContext, RecipeProfile
from content_factory.schemas.generator.run import RunStatus
from content_factory.services import production
from content_factory.services.generator import (
    choose_run_id,
    load_profile,
    parse_names,
    profile_slug,
    validate_count,
    write_report,
)
from content_factory.utils.run_id import profile_run_dir

if TYPE_CHECKING:
    from content_factory.config.settings import GeneratorSettings


def _run_group(
    group: tuple[RecipeProfile, ...],
    count: int,
    data_dir: Path,
    settings: GeneratorSettings,
    dry_run: bool,
    taken: set[str],
) -> tuple[str, int, int]:
    """Run one recipe group and write the report of every profile in it."""
    profiles = tuple(group)
    started = datetime.now(UTC)
    run_id = choose_run_id(settings.output_dir, list(profiles), started, taken=taken)
    context = PipelineContext(
        profiles=profiles,
        count=count,
        data_dir=data_dir,
        settings=settings,
        run_id=run_id,
        dry_run=dry_run,
        now=started,
    )
    layers = profiles[0].production
    result = production.run_pipeline(
        context,
        layers,
        producers=production.default_producers(),
        checks=production.default_checks(),
    )
    generated = failed = 0
    for profile in profiles:
        report = production.build_report(profile, layers, context, result)
        run_dir = profile_run_dir(
            settings.output_dir,
            profile.language,
            profile_slug(profile),
            run_id,
        )
        write_report(run_dir, report)
        generated += report.totals.generated
        failed += report.totals.failed
    return run_id, generated, failed


@flow(name="generate_batch")
def generate_batch(
    profiles: str = "dating/profile_2",
    count: int = 1,
    dry_run: bool = False,
    content_type: str | None = None,
) -> str:
    """Собрать партию контента по рецептам профилей и вернуть код исхода.

    Запуск ручной: расписания нет. Файловая блокировка не даёт двум партиям
    одновременно арендовать серверы; второй запуск сразу падает с
    ``GeneratorBusyError``, а не ждёт освобождения. Профили группируются по
    сигнатуре слоёв рецепта: одна группа — один ``run_id`` и один
    ``run_pipeline``. Группа одинаковых vast-профилей по-прежнему получает
    общий ``run_id`` и одну партию; профиль с несколькими независимыми
    цепочками (например, ``vast/video`` и ``card/photo``) выполняет их в
    одном прогоне. Отчёт ``run.json`` каждого профиля пишет flow один раз по
    результату конвейера.

    ``content_type`` оставляет в рецепте только слои этого типа, чтобы добор
    производил ровно один тип контента. Без параметра выполняются все слои,
    как в ручной партии. Слой, который зависит от отфильтрованного слоя
    другого типа, даёт ``ProfileError``: иначе конвейер получил бы вход,
    которого нет в прогоне.

    При ``dry_run`` каждый слой планируется через ``plan_layer``: для vast —
    looks/seeds/frames, для card — имена файлов. Producers, LLM, сеть и Vast
    не вызываются, курсор не двигается.

    Args:
        profiles: Имена профилей через запятую, например
            ``"dating/profile_2,dating/profile_1"``.
        count: Сколько items собрать на каждый профиль.
        dry_run: Построить план и записать ``run.json`` без Vast и ComfyUI.
        content_type: ``video`` или ``photo``; ``None`` — все слои рецепта.

    Raises:
        GeneratorError: ``count`` не положительное целое.
        ProfileError: Рецепт профиля пуст, называет неизвестный producer,
            не имеет слоя запрошенного типа или такой слой зависит от слоя
            другого типа.
    """
    settings = get_settings()
    prepare_data_dirs(settings)
    validate_count(count)
    selected = None if content_type is None else _parse_content_type(content_type)
    lock = try_acquire(lock_path(settings.data_dir / DB_DIR, GENERATOR_LOCK))
    if lock is None:
        raise GeneratorBusyError("генератор уже запущен: дождитесь конца текущей партии")
    try:
        generator_settings = load_generator_settings()
        loaded = [
            load_profile(name, settings.data_dir / PROFILES_DIR) for name in parse_names(profiles)
        ]
        if selected is not None:
            loaded = [
                replace(
                    profile,
                    production=_filter_recipe(profile.name, profile.production, selected),
                )
                for profile in loaded
            ]
        run_ids: list[str] = []
        generated = failed = 0
        taken: set[str] = set()
        for group in production.group_profiles(loaded):
            run_id, batch_generated, batch_failed = _run_group(
                group,
                count,
                settings.data_dir,
                generator_settings,
                dry_run,
                taken,
            )
            run_ids.append(run_id)
            taken.add(run_id)
            generated += batch_generated
            failed += batch_failed
    finally:
        lock.release()
    status = RunStatus.DRY_RUN.value if dry_run else ("failed" if failed else "done")
    logger.bind(
        flow="generate_batch",
        run_id=run_ids[0] if len(run_ids) == 1 else run_ids,
        status=status,
        generated=generated,
        failed=failed,
    ).info("Партия генератора завершена")
    runs = " ".join(f"run={run_id}" for run_id in run_ids)
    return f"{runs} status={status} generated={generated} failed={failed}"


def _parse_content_type(value: str) -> ContentType:
    """Parse the requested content type before any profile is loaded.

    Raises:
        ProfileError: The value is neither ``video`` nor ``photo``.
    """
    try:
        return ContentType(str(value).strip().lower())
    except ValueError as ex:
        allowed = ", ".join(item.value for item in ContentType)
        raise ProfileError(f"неизвестный content_type {value!r}: допустимы {allowed}") from ex


def _filter_recipe(
    name: str,
    layers: tuple[Layer, ...],
    selected: ContentType,
) -> tuple[Layer, ...]:
    """Keep the recipe layers of *selected* and reject dangling inputs.

    Args:
        name: Profile id used in error messages.
        layers: Recipe layers in execution order.
        selected: Content type the batch should produce.

    Returns:
        The layers that produce *selected*, in their original order.

    Raises:
        ProfileError: The recipe has no layer of that type, or a kept layer
            depends on a layer of another type that the filter would drop.
    """
    kept = tuple(layer for layer in layers if layer.output.value == selected.value)
    if not kept:
        raise ProfileError(f"у профиля {name} нет слоя type={selected.value!r}")
    names = {layer.name for layer in kept}
    for layer in kept:
        for source in layer.inputs:
            if source not in names:
                raise ProfileError(
                    f"у профиля {name} слой {layer.name!r} типа {selected.value!r} "
                    f"зависит от слоя {source!r} другого типа"
                )
    return kept
