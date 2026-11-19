"""Best-effort delivery of critical alerts to Telegram."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
from loguru import logger

from content_factory.config.settings import Settings
from content_factory.schemas.notify.alert import AlertKind
from content_factory.schemas.state.state import RuntimeState

_API = "https://api.telegram.org/bot{token}/sendMessage"
_TIMEOUT = 10.0


def send_critical(
    kind: AlertKind,
    text: str,
    *,
    settings: Settings,
    state: RuntimeState,
    cooldown: float,
    now: datetime | None = None,
) -> RuntimeState | None:
    """Отправить критичный алерт, если кулдаун этого вида истёк.

    Без ``TELEGRAM_BOT_TOKEN`` и ``TELEGRAM_CHAT_ID`` отправка выключена.
    Ошибка Telegram логируется и не прерывает flow.

    Args:
        kind: Вид алерта; кулдаун считается отдельно для каждого вида.
        text: Текст сообщения без секретов.
        settings: Настройки с токеном бота и чатом.
        state: Текущее состояние с последним отправленным алертом.
        cooldown: Минимальный интервал между одинаковыми видами, секунды.
        now: Момент проверки; по умолчанию текущее UTC-время.

    Returns:
        Состояние с обновлённым ``last_alert_*`` после успешной отправки,
        иначе ``None`` — алерт подавлен или доставка недоступна.
    """
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return None
    moment = now or datetime.now(UTC)
    if _in_cooldown(kind, state, cooldown, moment):
        return None
    if not _send(settings, kind, text):
        return None
    return replace(state, last_alert_at=moment, last_alert_kind=kind.value)


def _in_cooldown(
    kind: AlertKind,
    state: RuntimeState,
    cooldown: float,
    now: datetime,
) -> bool:
    """Return whether the same alert kind was sent too recently."""
    if state.last_alert_kind != kind.value or state.last_alert_at is None:
        return False
    return now - state.last_alert_at < timedelta(seconds=cooldown)


def _send(settings: Settings, kind: AlertKind, text: str) -> bool:
    """Post one message to Telegram and report whether it was accepted."""
    payload = {
        "chat_id": settings.telegram_chat_id,
        "text": f"[content-factory] {kind.value}: {text}",
        "disable_web_page_preview": True,
    }
    url = _API.format(token=settings.telegram_bot_token)
    try:
        response = httpx.post(url, json=payload, timeout=_TIMEOUT)
        response.raise_for_status()
    except Exception as ex:
        logger.bind(kind=kind.value, error=ex, error_type=type(ex).__name__).warning(
            "Не удалось отправить алерт {}", kind.value
        )
        return False
    return True
