"""Telegram Bot API transport for critical alerts."""

from __future__ import annotations

import httpx
from loguru import logger

from content_factory.config.settings import Settings
from content_factory.schemas.notify.alert import AlertKind

_API = "https://api.telegram.org/bot{token}/sendMessage"
_TIMEOUT = 10.0


def send_message(settings: Settings, kind: AlertKind, text: str) -> bool:
    """Post one alert to Telegram and report whether it was accepted.

    Delivery is best-effort: transport, HTTP and unexpected errors are logged
    with their context and turn into ``False`` instead of reaching the flow.
    The bot token is never logged.

    Args:
        settings: Settings with the bot token and the target chat.
        kind: Alert kind, used in the message text and the log context.
        text: Alert text without secrets.

    Returns:
        ``True`` when Telegram accepted the message, otherwise ``False``.
    """
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
