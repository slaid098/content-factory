"""Scheduled stock top-up flow."""

from __future__ import annotations

import sqlite3
from contextlib import suppress
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from loguru import logger
from prefect import flow

from content_factory.config.runtime import load_runtime_config
from content_factory.config.settings import Settings, load_settings, load_supplier_settings
from content_factory.constants import (
    CONFIG_DIR,
    CONFIG_FILENAME,
    CONTENT_DIR,
    DB_DIR,
    DB_FILENAME,
)
from content_factory.db.connection import connect
from content_factory.db.runtime_state import load_state, save_state
from content_factory.schemas.config.runtime import RuntimeConfig
from content_factory.schemas.notify.alert import AlertKind
from content_factory.schemas.state.state import RuntimeState
from content_factory.schemas.supplier.results import ProduceResult, StopReason
from content_factory.services.notify.service import send_critical
from content_factory.services.planner.service import plan_production
from content_factory.services.stock.service import count_stock
from content_factory.services.supplier.service import produce

_ZERO_RUNS_ALERT = 3
_BITBROWSER_MARKERS = ("bitbrowser", "mcp call failed")
_STORAGE_MARKERS = ("каталог", "записать", "файл запросов", "подготовить схему")


@flow(name="stock_topup")
def stock_topup() -> str:
    """Выполнить один тик добора стока и вернуть код исхода."""
    settings = load_settings()
    runtime = load_runtime_config(settings.data_dir / CONFIG_DIR / CONFIG_FILENAME)
    connection = connect(settings.data_dir / DB_DIR / DB_FILENAME)
    try:
        return _tick(connection, settings, runtime, now=datetime.now(UTC))
    finally:
        connection.close()


def _tick(
    connection: sqlite3.Connection,
    settings: Settings,
    runtime: RuntimeConfig,
    *,
    now: datetime,
) -> str:
    """Apply the pause and interval gates, then produce when stock is low."""
    state = load_state(connection)
    if _captcha_paused(state, now):
        logger.bind(
            outcome="captcha_pause",
            captcha_blocked_until=state.captcha_blocked_until,
        ).info("Капча ещё блокирует производство")
        return "captcha_pause"

    retry = state.captcha_streak > 0
    if not retry and not _interval_elapsed(state, runtime, now):
        logger.bind(outcome="idle", last_production_at=state.last_production_at).info(
            "Интервал производства ещё не истёк"
        )
        return "idle"

    counts = count_stock(settings.data_dir / CONTENT_DIR, runtime.languages)
    plan = plan_production(counts, runtime.languages, runtime.run.batch)
    if not plan:
        logger.bind(outcome="enough_stock", counts=counts).info("Остатки на цели")
        return "enough_stock"

    return _run_supplier(connection, settings, runtime, state, plan, now=now, retry=retry)


def _captcha_paused(state: RuntimeState, now: datetime) -> bool:
    """Return whether the captcha pause still blocks production."""
    return state.captcha_blocked_until is not None and now < state.captcha_blocked_until


def _interval_elapsed(state: RuntimeState, runtime: RuntimeConfig, now: datetime) -> bool:
    """Return whether the configured production interval has passed."""
    if state.last_production_at is None:
        return True
    return now >= state.last_production_at + timedelta(seconds=runtime.run.interval)


def _run_supplier(
    connection: sqlite3.Connection,
    settings: Settings,
    runtime: RuntimeConfig,
    state: RuntimeState,
    plan: dict[str, int],
    *,
    now: datetime,
    retry: bool,
) -> str:
    """Call the supplier and persist the state implied by its outcome."""
    logger.bind(plan=plan, captcha_retry=retry).info("Старт производства")
    try:
        supplier_settings = load_supplier_settings()
        result = produce(
            plan,
            runtime=runtime,
            settings=settings,
            supplier_settings=supplier_settings,
        )
    except Exception as ex:
        logger.bind(error=ex, error_type=type(ex).__name__).error("Поставщик упал")
        state = replace(state, last_production_at=now)
        state = _alert(
            AlertKind.SUPPLIER_CRASH,
            f"поставщик упал: {type(ex).__name__}",
            state,
            settings,
            runtime,
            now,
        )
        specific = _classify_crash(ex)
        if specific is not AlertKind.SUPPLIER_CRASH:
            state = _alert(specific, f"причина: {type(ex).__name__}", state, settings, runtime, now)
        save_state(connection, state)
        return "supplier_error"

    state = _apply_result(state, result, runtime, now)
    state = _alert_result(state, result, settings, runtime, now)
    save_state(connection, state)
    if result.stop_reason is StopReason.CAPTCHA:
        return "captcha"
    if result.produced_total > 0:
        return "captcha_retry" if retry else "produced"
    return "no_production"


def _apply_result(
    state: RuntimeState,
    result: ProduceResult,
    runtime: RuntimeConfig,
    now: datetime,
) -> RuntimeState:
    """Fold one supplier result into the persisted state."""
    state = replace(state, last_production_at=now)
    if result.stop_reason is StopReason.CAPTCHA:
        streak = state.captcha_streak + 1
        blocked = _blocked_until(result.blocked_until, runtime.captcha.pause, now)
        if streak >= runtime.captcha.streak:
            blocked = now + timedelta(seconds=runtime.captcha.long_pause)
        return replace(state, captcha_streak=streak, captcha_blocked_until=blocked)
    zero_runs = state.zero_runs + 1 if result.produced_total == 0 else 0
    return replace(
        state,
        captcha_streak=0,
        captcha_blocked_until=None,
        zero_runs=zero_runs,
    )


def _blocked_until(value: str | None, fallback_seconds: float, now: datetime) -> datetime:
    """Parse the supplier cooldown, falling back to the configured pause."""
    if value is not None:
        parsed: datetime | None = None
        with suppress(ValueError):
            parsed = datetime.fromisoformat(value)
        if parsed is not None:
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return now + timedelta(seconds=fallback_seconds)


def _alert_result(
    state: RuntimeState,
    result: ProduceResult,
    settings: Settings,
    runtime: RuntimeConfig,
    now: datetime,
) -> RuntimeState:
    """Send alerts for critical supplier outcomes."""
    if result.stop_reason is StopReason.CAPTCHA:
        if state.captcha_streak >= runtime.captcha.streak:
            state = _alert(
                AlertKind.CAPTCHA_STREAK,
                f"капча {state.captcha_streak} раз подряд",
                state,
                settings,
                runtime,
                now,
            )
        return state

    if result.stop_reason is StopReason.NO_QUERIES:
        state = _alert(
            AlertKind.NO_QUERIES,
            "нет поисковых запросов ни для одного языка",
            state,
            settings,
            runtime,
            now,
        )
    if result.stop_reason is StopReason.NO_PROGRESS:
        state = _alert(
            AlertKind.ALL_DOWNLOADS_FAILED,
            "ни одного скачивания за прогон",
            state,
            settings,
            runtime,
            now,
        )
    if state.zero_runs >= _ZERO_RUNS_ALERT:
        state = _alert(
            AlertKind.ZERO_PRODUCTION,
            f"{state.zero_runs} прогонов подряд без новых видео",
            state,
            settings,
            runtime,
            now,
        )
    return state


def _alert(
    kind: AlertKind,
    text: str,
    state: RuntimeState,
    settings: Settings,
    runtime: RuntimeConfig,
    now: datetime,
) -> RuntimeState:
    """Send one alert and return the state with its cooldown stamp."""
    updated = send_critical(
        kind,
        text,
        settings=settings,
        state=state,
        cooldown=runtime.alerts.cooldown,
        now=now,
    )
    return state if updated is None else updated


def _classify_crash(error: Exception) -> AlertKind:
    """Map a supplier exception to the most specific alert kind."""
    message = str(error).lower()
    if any(marker in message for marker in _BITBROWSER_MARKERS):
        return AlertKind.BITBROWSER_UNAVAILABLE
    if any(marker in message for marker in _STORAGE_MARKERS):
        return AlertKind.STORAGE_ERROR
    return AlertKind.SUPPLIER_CRASH
