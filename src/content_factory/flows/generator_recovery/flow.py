"""Scheduled recovery of orphaned Vast.ai generator servers."""

from __future__ import annotations

from time import time

from loguru import logger
from prefect import flow

from content_factory.config.settings import (
    Settings,
    get_settings,
    load_generator_settings,
    prepare_data_dirs,
)
from content_factory.constants import DB_DIR, DB_FILENAME
from content_factory.db.connection import connect
from content_factory.db.errors import DatabaseError
from content_factory.db.lock import GENERATOR_LOCK, lock_path, try_acquire
from content_factory.db.run_journal import RunJournalWriter
from content_factory.exceptions.generator import RentError
from content_factory.flows.generator_recovery import recovery
from content_factory.services.generator import VastProvider, resume


@flow(name="generator_recovery")
def generator_recovery(provider: VastProvider | None = None) -> str:
    """Погасить осиротевшие серверы генератора и вернуть код исхода.

    Тик берёт ``GENERATOR_LOCK`` без ожидания: занятый lock означает живую
    генерацию, и flow выходит с ``busy``, не трогая Vast.ai. При свободном
    lock незавершённые строки журнала никому не принадлежат, поэтому
    помеченные инстансы старше grace-периода уничтожаются, а строки без
    инстанса становятся ``lost``. Ошибки Vast.ai и БД логируются и дают код
    исхода вместо падения worker: следующий тик повторит проход.

    Args:
        provider: Клиент Vast.ai; по умолчанию строится из настроек.

    Returns:
        ``busy``, ``vast_unavailable``, ``db_unavailable`` или
        ``destroyed=<n> lost=<n> failed=<n>``.
    """
    settings = get_settings()
    prepare_data_dirs(settings)
    lock = try_acquire(lock_path(settings.data_dir / DB_DIR, GENERATOR_LOCK))
    if lock is None:
        logger.bind().info(
            "Восстановление серверов: генератор занят, Vast.ai не трогаем; исход busy"
        )
        return "busy"
    try:
        return _recover(settings, provider)
    finally:
        lock.release()


def _recover(settings: Settings, provider: VastProvider | None) -> str:
    """Open the journal and run one recovery pass with outcome handling."""
    try:
        connection = connect(settings.data_dir / DB_DIR / DB_FILENAME)
    except DatabaseError as ex:
        logger.bind(error=str(ex), error_type=type(ex).__name__).error(
            f"Восстановление серверов: БД недоступна: {ex}; исход db_unavailable"
        )
        return "db_unavailable"
    try:
        try:
            vast = provider or _provider()
            outcomes = resume.resume_unfinished(
                connection,
                data_dir=settings.data_dir,
                settings=load_generator_settings(),
                provider=vast,
                journal=RunJournalWriter(settings.data_dir / DB_DIR / DB_FILENAME),
            )
            for item in outcomes:
                logger.bind(
                    run_id=item.run_id,
                    status=item.status,
                    generated=item.generated,
                    failed=item.failed,
                ).info("Восстановление: незавершённый ран продолжен")
            outcome = recovery.recover(connection, vast, now=time())
        except DatabaseError as ex:
            logger.bind(error=str(ex), error_type=type(ex).__name__).error(
                f"Восстановление серверов: БД недоступна: {ex}; исход db_unavailable"
            )
            return "db_unavailable"
        except RentError as ex:
            logger.bind(error=str(ex), error_type=type(ex).__name__).error(
                f"Восстановление серверов: Vast.ai недоступен: {ex}; исход vast_unavailable"
            )
            return "vast_unavailable"
    finally:
        connection.close()
    logger.bind(
        destroyed=outcome.destroyed,
        lost=outcome.lost,
        failed=outcome.failed,
    ).info(
        "Восстановление серверов: уничтожено {}, потеряно {}, ошибок {}; исход done",
        outcome.destroyed,
        outcome.lost,
        outcome.failed,
    )
    return f"destroyed={outcome.destroyed} lost={outcome.lost} failed={outcome.failed}"


def _provider() -> VastProvider:
    """Build the Vast.ai provider from the generator settings."""
    settings = load_generator_settings()
    token = settings.vast_api_token.get_secret_value() if settings.vast_api_token else ""
    return VastProvider(token)


__all__ = ["generator_recovery"]
