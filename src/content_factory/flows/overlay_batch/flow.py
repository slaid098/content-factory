"""Prefect flow of one manual overlay batch."""

from __future__ import annotations

from loguru import logger
from prefect import flow

from content_factory.config.settings import get_settings, prepare_data_dirs
from content_factory.constants import DB_DIR
from content_factory.db.lock import GENERATOR_LOCK, lock_path, try_acquire
from content_factory.exceptions.generator import GeneratorBusyError
from content_factory.schemas.generator.overlay import OverlayRun
from content_factory.services.generator.overlay_batch import generate_overlays

_FLOW = "overlay_batch"


@flow(name=_FLOW)
def overlay_batch(
    profile: str = "overlay/profile_17",
    dry_run: bool = False,
    force: bool = False,
) -> str:
    """Сгенерировать и упаковать партию оверлеев одного профиля.

    Поток ручной: профиль и промпты создаёт агент командой ``overlay_cli``,
    а flow отвечает за запуск под общим ``GENERATOR_LOCK`` и отчёт. В
    dry-run сервер не арендуется, поэтому блокировка не нужна.

    Args:
        profile: Имя профиля, например ``overlay/profile_17``.
        dry_run: Построить план и измерить готовые клипы без Vast.ai.
        force: Перегенерировать все клипы, даже прошедшие проверку.

    Returns:
        Короткая строка исхода с числом принятых клипов и путями архива.

    Raises:
        GeneratorBusyError: Партию уже запустил другой процесс.
        GeneratorError: Профиль, промпты или упаковка не прошли проверку.
    """
    settings = get_settings()
    prepare_data_dirs(settings)
    if dry_run:
        run = generate_overlays(profile, data_dir=settings.data_dir, dry_run=True)
        _log(run, dry_run=True)
        return _summary(run, dry_run=True)
    lock = try_acquire(lock_path(settings.data_dir / DB_DIR, GENERATOR_LOCK))
    if lock is None:
        raise GeneratorBusyError("генератор уже запущен: дождитесь конца текущей партии")
    try:
        run = generate_overlays(profile, data_dir=settings.data_dir, force=force)
    finally:
        lock.release()
    _log(run, dry_run=False)
    return _summary(run, dry_run=False)


def _summary(run: OverlayRun, *, dry_run: bool) -> str:
    """Return the short outcome of the batch."""
    if dry_run:
        return (
            f"profile={run.plan.profile} status=dry_run "
            f"planned={len(run.plan.items)} accepted={len(run.accepted)}"
        )
    archive = run.package.archive if run.package is not None else "none"
    return (
        f"profile={run.plan.profile} status=done "
        f"accepted={len(run.accepted)} rejected={len(run.rejected)} archive={archive}"
    )


def _log(run: OverlayRun, *, dry_run: bool) -> None:
    """Log the outcome with the profile and run identity."""
    logger.bind(
        flow=_FLOW,
        profile=run.plan.profile,
        run_id=run.plan.run_id,
        planned=len(run.plan.items),
        accepted=len(run.accepted),
        rejected=len(run.rejected),
        status="dry_run" if dry_run else "done",
    ).info("Overlay-партия завершена")


__all__ = ["overlay_batch"]
