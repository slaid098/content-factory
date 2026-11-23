from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from loguru import logger

from content_factory.config.settings import Settings
from content_factory.schemas.notify.alert import AlertKind
from content_factory.services.notify import client as notify_client
from content_factory.services.notify import service

_NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def _settings(**overrides: object) -> Settings:
    base = Settings(
        data_dir=Path("unused"),
        telegram_bot_token="token",
        telegram_chat_id="42",
    )
    return base.model_copy(update=overrides)


def _patch_send(
    monkeypatch: pytest.MonkeyPatch,
    *,
    result: bool = True,
) -> list[tuple[Settings, AlertKind, str]]:
    calls: list[tuple[Settings, AlertKind, str]] = []

    def fake_send(settings: Settings, kind: AlertKind, text: str) -> bool:
        calls.append((settings, kind, text))
        return result

    monkeypatch.setattr(notify_client, "send_message", fake_send)
    return calls


class _Response:
    def raise_for_status(self) -> None:
        return None


class _BrokenResponse:
    def raise_for_status(self) -> None:
        request = httpx.Request("POST", "https://api.telegram.org")
        response = httpx.Response(500, request=request)
        raise httpx.HTTPStatusError("bad", request=request, response=response)


def _patch_post(
    monkeypatch: pytest.MonkeyPatch,
    *,
    response: object | None = None,
    error: Exception | None = None,
) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def fake_post(
        url: str,
        *,
        json: dict[str, object],
        timeout: float,
    ) -> object:
        calls.append({"url": url, "json": json, "timeout": timeout})
        if error is not None:
            raise error
        return response if response is not None else _Response()

    monkeypatch.setattr(notify_client.httpx, "post", fake_post)
    return calls


def test_without_credentials_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_send(monkeypatch)

    result = service.send_critical(
        AlertKind.SUPPLIER_CRASH,
        "boom",
        settings=_settings(telegram_bot_token=None),
        last_sent={},
        cooldown=3600.0,
        now=_NOW,
    )

    assert result is None
    assert calls == []


def test_sends_first_alert_and_returns_send_time(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_send(monkeypatch)

    result = service.send_critical(
        AlertKind.CAPTCHA_STREAK,
        "капча",
        settings=_settings(),
        last_sent={},
        cooldown=3600.0,
        now=_NOW,
    )

    assert result == _NOW
    assert len(calls) == 1
    _, kind, text = calls[0]
    assert kind is AlertKind.CAPTCHA_STREAK
    assert text == "капча"


def test_same_kind_within_cooldown_is_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_send(monkeypatch)
    last_sent = {"captcha_streak": _NOW - timedelta(hours=1)}

    result = service.send_critical(
        AlertKind.CAPTCHA_STREAK,
        "капча",
        settings=_settings(),
        last_sent=last_sent,
        cooldown=6 * 3600.0,
        now=_NOW,
    )

    assert result is None
    assert calls == []


def test_cooldown_boundary_allows_send(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_send(monkeypatch)
    last_sent = {"captcha_streak": _NOW - timedelta(hours=6)}

    result = service.send_critical(
        AlertKind.CAPTCHA_STREAK,
        "капча",
        settings=_settings(),
        last_sent=last_sent,
        cooldown=6 * 3600.0,
        now=_NOW,
    )

    assert result == _NOW
    assert len(calls) == 1


def test_other_kind_bypasses_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_send(monkeypatch)
    last_sent = {"zero_production": _NOW - timedelta(minutes=1)}

    result = service.send_critical(
        AlertKind.CAPTCHA_STREAK,
        "капча",
        settings=_settings(),
        last_sent=last_sent,
        cooldown=6 * 3600.0,
        now=_NOW,
    )

    assert result == _NOW
    assert len(calls) == 1


def test_alternating_kinds_do_not_reset_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_send(monkeypatch)
    last_sent = {
        "captcha_streak": _NOW - timedelta(hours=1),
        "supplier_crash": _NOW - timedelta(minutes=10),
    }

    result = service.send_critical(
        AlertKind.CAPTCHA_STREAK,
        "капча",
        settings=_settings(),
        last_sent=last_sent,
        cooldown=6 * 3600.0,
        now=_NOW,
    )

    assert result is None
    assert calls == []


def test_kind_without_timestamp_allows_send(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_send(monkeypatch)

    result = service.send_critical(
        AlertKind.CAPTCHA_STREAK,
        "капча",
        settings=_settings(),
        last_sent={"supplier_crash": _NOW - timedelta(minutes=1)},
        cooldown=6 * 3600.0,
        now=_NOW,
    )

    assert result == _NOW
    assert len(calls) == 1


def test_unsent_alert_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_send(monkeypatch, result=False)

    result = service.send_critical(
        AlertKind.SUPPLIER_CRASH,
        "boom",
        settings=_settings(),
        last_sent={},
        cooldown=3600.0,
        now=_NOW,
    )

    assert result is None
    assert len(calls) == 1


def test_client_posts_alert_message(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_post(monkeypatch)

    result = notify_client.send_message(_settings(), AlertKind.CAPTCHA_STREAK, "капча")

    assert result is True
    assert len(calls) == 1
    assert calls[0]["url"] == "https://api.telegram.org/bottoken/sendMessage"
    assert calls[0]["timeout"] == 10.0
    payload = calls[0]["json"]
    assert isinstance(payload, dict)
    assert payload == {
        "chat_id": "42",
        "text": "[content-factory] captcha_streak: капча",
        "disable_web_page_preview": True,
    }


def test_client_http_error_returns_false_and_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_post(monkeypatch, response=_BrokenResponse())
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(message.record["message"]), level="WARNING")

    try:
        result = notify_client.send_message(_settings(), AlertKind.SUPPLIER_CRASH, "boom")
    finally:
        logger.remove(sink)

    assert result is False
    assert messages == ["Не удалось отправить алерт supplier_crash"]


@pytest.mark.parametrize(
    "error",
    [httpx.ConnectError("unreachable"), httpx.TimeoutException("timeout")],
    ids=["connect", "timeout"],
)
def test_client_transport_error_returns_false(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    _patch_post(monkeypatch, error=error)

    result = notify_client.send_message(_settings(), AlertKind.SUPPLIER_CRASH, "boom")

    assert result is False


def test_client_unexpected_error_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_post(monkeypatch, error=ValueError("invalid token"))

    result = notify_client.send_message(_settings(), AlertKind.SUPPLIER_CRASH, "boom")

    assert result is False
