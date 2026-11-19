from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from content_factory.config.settings import Settings
from content_factory.schemas.notify.alert import AlertKind
from content_factory.schemas.state.state import RuntimeState
from content_factory.services.notify import service

_NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


def _settings(**overrides: object) -> Settings:
    base = Settings(
        data_dir=Path("unused"),
        telegram_bot_token="token",
        telegram_chat_id="42",
    )
    return base.model_copy(update=overrides)


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
    broken: bool = False,
) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def fake_post(
        url: str,
        *,
        json: dict[str, object],
        timeout: float,
    ) -> object:
        calls.append({"url": url, "json": json, "timeout": timeout})
        return _BrokenResponse() if broken else _Response()

    monkeypatch.setattr(service.httpx, "post", fake_post)
    return calls


def _state(**overrides: object) -> RuntimeState:
    from dataclasses import replace

    return replace(RuntimeState.empty(), **overrides)


def test_without_credentials_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_post(monkeypatch)

    result = service.send_critical(
        AlertKind.SUPPLIER_CRASH,
        "boom",
        settings=_settings(telegram_bot_token=None),
        state=_state(),
        cooldown=3600.0,
        now=_NOW,
    )

    assert result is None
    assert calls == []


def test_sends_first_alert_and_stamps_state(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_post(monkeypatch)

    result = service.send_critical(
        AlertKind.CAPTCHA_STREAK,
        "капча",
        settings=_settings(),
        state=_state(),
        cooldown=3600.0,
        now=_NOW,
    )

    assert result is not None
    assert result.last_alert_at == _NOW
    assert result.last_alert_kind == "captcha_streak"
    assert len(calls) == 1
    assert calls[0]["url"] == "https://api.telegram.org/bottoken/sendMessage"
    payload = calls[0]["json"]
    assert isinstance(payload, dict)
    assert payload["chat_id"] == "42"
    assert "captcha_streak" in str(payload["text"])
    assert "капча" in str(payload["text"])


def test_same_kind_within_cooldown_is_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_post(monkeypatch)
    state = _state(
        last_alert_at=_NOW - timedelta(hours=1),
        last_alert_kind="captcha_streak",
    )

    result = service.send_critical(
        AlertKind.CAPTCHA_STREAK,
        "капча",
        settings=_settings(),
        state=state,
        cooldown=6 * 3600.0,
        now=_NOW,
    )

    assert result is None
    assert calls == []


def test_cooldown_boundary_allows_send(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_post(monkeypatch)
    state = _state(
        last_alert_at=_NOW - timedelta(hours=6),
        last_alert_kind="captcha_streak",
    )

    result = service.send_critical(
        AlertKind.CAPTCHA_STREAK,
        "капча",
        settings=_settings(),
        state=state,
        cooldown=6 * 3600.0,
        now=_NOW,
    )

    assert result is not None
    assert len(calls) == 1


def test_other_kind_bypasses_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_post(monkeypatch)
    state = _state(
        last_alert_at=_NOW - timedelta(minutes=1),
        last_alert_kind="zero_production",
    )

    result = service.send_critical(
        AlertKind.CAPTCHA_STREAK,
        "капча",
        settings=_settings(),
        state=state,
        cooldown=6 * 3600.0,
        now=_NOW,
    )

    assert result is not None
    assert result.last_alert_kind == "captcha_streak"
    assert len(calls) == 1


def test_kind_without_timestamp_allows_send(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_post(monkeypatch)
    state = _state(last_alert_kind="captcha_streak", last_alert_at=None)

    result = service.send_critical(
        AlertKind.CAPTCHA_STREAK,
        "капча",
        settings=_settings(),
        state=state,
        cooldown=6 * 3600.0,
        now=_NOW,
    )

    assert result is not None
    assert len(calls) == 1


def test_network_error_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(
        url: str,
        *,
        json: dict[str, object],
        timeout: float,
    ) -> object:
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr(service.httpx, "post", fake_post)

    result = service.send_critical(
        AlertKind.SUPPLIER_CRASH,
        "boom",
        settings=_settings(),
        state=_state(),
        cooldown=3600.0,
        now=_NOW,
    )

    assert result is None


def test_http_error_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_post(monkeypatch, broken=True)

    result = service.send_critical(
        AlertKind.SUPPLIER_CRASH,
        "boom",
        settings=_settings(),
        state=_state(),
        cooldown=3600.0,
        now=_NOW,
    )

    assert result is None


def test_unexpected_send_error_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(
        url: str,
        *,
        json: dict[str, object],
        timeout: float,
    ) -> object:
        raise ValueError("invalid token")

    monkeypatch.setattr(service.httpx, "post", fake_post)

    result = service.send_critical(
        AlertKind.SUPPLIER_CRASH,
        "boom",
        settings=_settings(),
        state=_state(),
        cooldown=3600.0,
        now=_NOW,
    )

    assert result is None
