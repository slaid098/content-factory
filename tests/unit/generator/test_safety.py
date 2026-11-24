"""Clef safety check: preflight, frame points, thresholds and fail-closed errors."""

from __future__ import annotations

import base64
import json
import random
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.unit.generator.helpers import fixture_profile

from content_factory.config.settings import GeneratorSettings
from content_factory.exceptions.generator import GeneratorError, SafetyError
from content_factory.services.generator import media, safety
from content_factory.services.generator.plan import _PlannedItem, plan_batch

_KEYS = ("back", "short_skirt", "cleavage", "all_skin", "nude")


def _settings(**overrides: Any) -> GeneratorSettings:
    values: dict[str, Any] = {
        "OPENAI_BASE_URL": "http://llm.test/v1",
        "OPENAI_API_KEY": "secret",
        "clef_model": "clef",
    }
    values.update(overrides)
    return GeneratorSettings(**values)


def _answers(**scores: float) -> dict[str, Any]:
    values = dict.fromkeys(_KEYS, 0.01)
    values.update(scores)
    return {"answers": {key: {"noul": value} for key, value in values.items()}}


def _client(handler: Any, settings: GeneratorSettings | None = None) -> safety.ClefClient:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return safety.ClefClient(settings or _settings(), client=http)


def test_frame_points_are_uniform_and_include_the_last_frame() -> None:
    points = safety.frame_points(5.0, 12)

    assert len(points) == 12
    assert points[0] == pytest.approx(0.1)
    assert points[-1] == pytest.approx(4.95)
    assert list(points) == sorted(points)
    assert len(set(points)) == 12


def test_short_video_gets_one_frame_and_zero_duration_fails() -> None:
    assert safety.frame_points(0.4, 12) == (pytest.approx(0.35),)

    with pytest.raises(SafetyError, match="положительной"):
        safety.frame_points(0.0, 12)


def test_client_sends_the_systemone_payload_and_parses_answers() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_answers(back=0.04), request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = safety.ClefClient(_settings(), client=http)
        scores = client.scores(b"\x89PNG-frame")

    assert seen["url"] == "http://llm.test/v1/systemone"
    assert seen["authorization"] == "Bearer secret"
    body = seen["body"]
    assert body["model"] == "clef"
    assert body["state"]
    assert base64.b64decode(body["images"][0]) == b"\x89PNG-frame"
    assert set(body["questions"]) == set(_KEYS)
    assert all(question["type"] == "noul" for question in body["questions"].values())
    assert scores["back"] == pytest.approx(0.04)


@pytest.mark.parametrize(
    "payload",
    [
        {"answers": {key: {"noul": 0.1} for key in _KEYS if key != "nude"}},
        {"answers": {key: {"noul": 0.1} for key in _KEYS} | {"nude": {"noul": "high"}}},
        {"answers": {key: {"noul": 1.5} for key in _KEYS}},
        {"answers": []},
        {},
    ],
)
def test_invalid_answers_fail_closed(payload: dict[str, Any]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload, request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = safety.ClefClient(_settings(), client=http)
        with pytest.raises(SafetyError):
            client.scores(b"png")


def test_5xx_is_retried_once_then_fails() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(503, text="busy", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = safety.ClefClient(_settings(), client=http, sleep=sleeps.append)
        with pytest.raises(SafetyError, match="2 попытки"):
            client.scores(b"png")

    assert len(calls) == 2
    assert sleeps == [1.0]


def test_network_error_is_retried_once() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ConnectError("down", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = safety.ClefClient(_settings(), client=http, sleep=lambda seconds: None)
        with pytest.raises(SafetyError, match="не ответил"):
            client.scores(b"png")

    assert len(calls) == 2


def test_client_error_is_not_retried() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401, text="no key", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = safety.ClefClient(_settings(), client=http)
        with pytest.raises(SafetyError, match="код 401"):
            client.scores(b"png")

    assert len(calls) == 1


def test_preflight_makes_one_synthetic_request() -> None:
    images: list[bytes] = []

    class _Clef:
        def scores(self, png: bytes) -> dict[str, float]:
            images.append(png)
            return dict.fromkeys(_KEYS, 0.0)

    checker = safety.FrameChecker(_settings(), client=_Clef())

    checker.preflight()

    assert len(images) == 1
    assert images[0].startswith(b"\x89PNG")


def test_frame_reason_uses_the_nude_threshold() -> None:
    settings = _settings()
    safe = dict.fromkeys(_KEYS, 0.0)

    assert safety.frame_reason(safe, settings) is None
    assert safety.frame_reason(safe | {"back": 0.5}, settings) is not None
    assert safety.frame_reason(safe | {"short_skirt": 0.49}, settings) is None
    assert safety.frame_reason(safe | {"nude": 0.3}, settings) is not None
    assert safety.frame_reason(safe | {"nude": 0.29}, settings) is None


def _item(tmp_path: Path) -> _PlannedItem:
    profile = fixture_profile(tmp_path / "profiles", looks=20)
    return plan_batch(profile, 1, "run", rng=random.Random(1))[0]


def test_verify_checks_twelve_frames_and_reports_the_bad_one(tmp_path: Path, monkeypatch) -> None:
    item = _item(tmp_path)
    moments: list[float] = []
    timeouts: list[float] = []
    calls: list[bytes] = []

    def probe(path: Path, *, timeout: float) -> media._MediaInfo:
        timeouts.append(timeout)
        return media._MediaInfo(
            path=path,
            duration=5.0,
            width=512,
            height=896,
            has_video=True,
            has_audio=True,
            size=20_000,
        )

    def frame(path: Path, seconds: float, *, timeout: float) -> bytes:
        del path
        moments.append(seconds)
        timeouts.append(timeout)
        return b"png"

    class _Clef:
        def scores(self, png: bytes) -> dict[str, float]:
            calls.append(png)
            if len(calls) < 12:
                return dict.fromkeys(_KEYS, 0.0)
            return dict.fromkeys(_KEYS, 0.0) | {"back": 0.9}

    monkeypatch.setattr(media, "probe", probe)
    monkeypatch.setattr(media, "frame_png", frame)

    reason = safety.verify_source(_Clef(), _settings(), item, tmp_path / "source.mp4", 1)

    assert len(moments) == 12
    assert moments[-1] == pytest.approx(4.95)
    assert timeouts == [20.0] * 13
    assert reason is not None
    assert "кадр 12/12" in reason
    assert "back 0.90" in reason


def test_short_source_is_checked_with_one_frame(tmp_path: Path, monkeypatch) -> None:
    item = _item(tmp_path)
    moments: list[float] = []

    def probe(path: Path, *, timeout: float) -> media._MediaInfo:
        del timeout
        return media._MediaInfo(
            path=path,
            duration=0.4,
            width=512,
            height=896,
            has_video=True,
            has_audio=True,
            size=20_000,
        )

    def frame(path: Path, seconds: float, *, timeout: float) -> bytes:
        del path, timeout
        moments.append(seconds)
        return b"png"

    class _Clef:
        def scores(self, png: bytes) -> dict[str, float]:
            del png
            return dict.fromkeys(_KEYS, 0.0)

    monkeypatch.setattr(media, "probe", probe)
    monkeypatch.setattr(media, "frame_png", frame)

    assert safety.verify_source(_Clef(), _settings(), item, tmp_path / "source.mp4", 1) is None
    assert moments == [pytest.approx(0.35)]


def test_frame_extraction_error_fails_closed(tmp_path: Path, monkeypatch) -> None:
    item = _item(tmp_path)
    timeouts: list[float] = []

    def probe(path: Path, *, timeout: float) -> media._MediaInfo:
        timeouts.append(timeout)
        return media._MediaInfo(
            path=path,
            duration=5.0,
            width=512,
            height=896,
            has_video=True,
            has_audio=True,
            size=20_000,
        )

    def frame(path: Path, seconds: float, *, timeout: float) -> bytes:
        del path, seconds
        timeouts.append(timeout)
        raise GeneratorError("ffmpeg упал")

    class _Clef:
        def scores(self, png: bytes) -> dict[str, float]:
            del png
            raise AssertionError("no frame should reach Clef")

    monkeypatch.setattr(media, "probe", probe)
    monkeypatch.setattr(media, "frame_png", frame)

    with pytest.raises(SafetyError, match="не извлечён"):
        safety.verify_source(_Clef(), _settings(), item, tmp_path / "source.mp4", 1)

    assert timeouts == [20.0, 20.0]


def test_disabled_safety_skips_the_probe(tmp_path: Path, monkeypatch) -> None:
    item = _item(tmp_path)

    def probe(path: Path, *, timeout: float) -> media._MediaInfo:
        del path, timeout
        raise AssertionError("disabled safety must not probe the source")

    monkeypatch.setattr(media, "probe", probe)

    assert (
        safety.verify_source(
            _ClefNoCalls(),
            _settings(safety_enabled=False),
            item,
            tmp_path / "source.mp4",
            1,
        )
        is None
    )


class _ClefNoCalls:
    """Clef transport that must never be asked."""

    def scores(self, png: bytes) -> dict[str, float]:
        del png
        raise AssertionError("disabled safety must not call Clef")


def test_client_sends_no_authorization_without_a_key() -> None:
    seen: dict[str, Any] = {}
    settings = GeneratorSettings(OPENAI_BASE_URL="http://llm.test/v1")

    def handler(request: httpx.Request) -> httpx.Response:
        seen["has_authorization"] = "Authorization" in request.headers
        return httpx.Response(200, json=_answers(), request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        safety.ClefClient(settings, client=http).scores(b"png")

    assert seen["has_authorization"] is False


def test_client_rejects_a_key_with_control_characters() -> None:
    settings = GeneratorSettings(
        OPENAI_BASE_URL="http://llm.test/v1",
        OPENAI_API_KEY="bad\nkey",
    )

    with pytest.raises(SafetyError, match="недопустимые символы"):
        safety.ClefClient(settings).scores(b"png")


def test_unreadable_source_fails_closed(tmp_path: Path, monkeypatch) -> None:
    item = _item(tmp_path)
    monkeypatch.setattr(media, "probe", lambda path, *, timeout: None)

    with pytest.raises(SafetyError, match="не удалось прочитать"):
        safety.verify_source(_ClefNoCalls(), _settings(), item, tmp_path / "source.mp4", 1)


def test_single_requested_frame_is_the_last_one_and_checker_builds_its_client() -> None:
    assert safety.frame_points(5.0, 1) == (pytest.approx(4.95),)
    assert isinstance(safety.FrameChecker(_settings())._client, safety.ClefClient)


def test_clef_request_timeout_is_short() -> None:
    client = safety.ClefClient(_settings())

    assert client._timeout == 30.0


def test_any_request_error_is_retried_once() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.DecodingError("bad body", request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = safety.ClefClient(_settings(), client=http, sleep=lambda seconds: None)
        with pytest.raises(SafetyError, match="не ответил"):
            client.scores(b"png")

    assert len(calls) == 2


def test_rate_limit_is_retried_once_then_succeeds() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, text="slow down", request=request)
        return httpx.Response(200, json=_answers(back=0.04), request=request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = safety.ClefClient(_settings(), client=http, sleep=sleeps.append)
        scores = client.scores(b"png")

    assert len(calls) == 2
    assert sleeps == [1.0]
    assert scores["back"] == pytest.approx(0.04)
