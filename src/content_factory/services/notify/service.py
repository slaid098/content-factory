"""Best-effort delivery of critical alerts to Telegram."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from content_factory.config.settings import Settings
from content_factory.schemas.notify.alert import AlertKind
from content_factory.services.notify import client


def send_critical(
    kind: AlertKind,
    text: str,
    *,
    settings: Settings,
    last_sent: Mapping[str, datetime],
    cooldown: float,
    now: datetime | None = None,
) -> datetime | None:
    """Отправить критичный алерт, если кулдаун этого вида истёк.

    Без ``TELEGRAM_BOT_TOKEN`` и ``TELEGRAM_CHAT_ID`` отправка выключена.
    Ошибка Telegram логируется клиентом и не прерывает flow.

    Args:
        kind: Вид алерта; кулдаун считается отдельно для каждого вида.
        text: Текст сообщения без секретов.
        settings: Настройки с токеном бота и чатом.
        last_sent: Время последней успешной отправки по каждому виду.
        cooldown: Минимальный интервал между одинаковыми видами, секунды.
        now: Момент проверки; по умолчанию текущее UTC-время.

    Returns:
        Момент успешной отправки, иначе ``None`` — алерт подавлен или
        доставка недоступна.
    """
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        return None
    moment = now or datetime.now(UTC)
    if _in_cooldown(kind, last_sent, cooldown, moment):
        return None
    if not client.send_message(settings, kind, text):
        return None
    return moment


def _in_cooldown(
    kind: AlertKind,
    last_sent: Mapping[str, datetime],
    cooldown: float,
    now: datetime,
) -> bool:
    """Return whether the same alert kind was sent too recently."""
    last = last_sent.get(kind.value)
    return last is not None and now - last < timedelta(seconds=cooldown)
