from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from loguru import logger
from pydantic import SecretStr

from content_factory.config.settings import TitlesSettings, load_titles_settings
from content_factory.constants import CONTENT_DIR, DB_DIR, DB_FILENAME
from content_factory.db import titles as titles_db
from content_factory.db.titles import ensure_schema, load_titles, recent_titles, save_title
from content_factory.db.video_meta import VideoMeta, load_video_meta
from content_factory.exceptions import ConfigError
from content_factory.flows.titles import flow as titles_flow
from content_factory.services.titles import assign_titles
from content_factory.services.titles import client as titles_client
from content_factory.services.titles import frames as titles_frames
from content_factory.services.titles import service as titles_service
from content_factory.services.titles.client import (
    TitleClientError,
    TitleError,
    TransientTitleError,
    generate_titles,
)
from content_factory.services.titles.frames import extract_frames, frame_times
from content_factory.services.titles.service import TitlePassResult
from content_factory.services.titles.text import (
    clean_title,
    description_title_key,
    normalize_title_key,
    validate_title,
)
from delivery_helpers import add_downloaded_video, add_title, touch_mp4

_NOW = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)
_SECRET = "test-secret"


def _settings(**overrides: object) -> TitlesSettings:
    values: dict[str, object] = {"api_key": SecretStr(_SECRET)}
    values.update(overrides)
    return TitlesSettings(**values)  # type: ignore[arg-type]


def _db_path(data_dir: Path) -> Path:
    return data_dir / DB_DIR / DB_FILENAME


def _completion(*titles: str, content: str | None = None) -> dict[str, Any]:
    text = content if content is not None else json.dumps({"titles": list(titles)})
    return {"choices": [{"message": {"content": text}}]}


def _http_client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler)


# --- titles table -----------------------------------------------------------


def test_save_title_inserts_once_and_rejects_duplicates(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)
    db_path.parent.mkdir(parents=True)
    connection = sqlite3.connect(db_path)
    try:
        ensure_schema(connection)
        assert save_title(connection, "a" * 64, "Утренний свет на кухне", "свет", "2026-05-01")
        assert (
            save_title(connection, "a" * 64, "Другой вариант утра", "утро", "2026-05-01") is False
        )
        assert (
            save_title(connection, "b" * 64, "Третий вариант утра", "свет", "2026-05-01") is False
        )
    finally:
        connection.close()

    assert load_titles(db_path, ["a" * 64, "b" * 64]) == {"a" * 64: "Утренний свет на кухне"}


def test_load_titles_reads_more_than_one_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = _db_path(tmp_path)
    db_path.parent.mkdir(parents=True)
    titles = ("Первое название утра", "Второе название утра", "Третье название утра")
    for index, title in enumerate(titles):
        add_title(db_path, f"{index:064x}", title)
    hashes = [f"{index:064x}" for index in range(5)]
    batches: list[list[str]] = []
    original = titles_db._load_batch

    def spy(connection: sqlite3.Connection, batch: list[str]) -> dict[str, str]:
        batches.append(list(batch))
        return original(connection, batch)

    monkeypatch.setattr(titles_db, "_BATCH_SIZE", 2)
    monkeypatch.setattr(titles_db, "_load_batch", spy)

    assert load_titles(db_path, hashes) == {
        f"{index:064x}": title for index, title in enumerate(titles)
    }
    assert [len(batch) for batch in batches] == [2, 2, 1]
    assert [sha256 for batch in batches for sha256 in batch] == hashes


def test_load_titles_skips_empty_titles(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)
    db_path.parent.mkdir(parents=True)
    connection = sqlite3.connect(db_path)
    try:
        ensure_schema(connection)
        connection.execute(
            "INSERT INTO titles (sha256, title, norm, created_at) VALUES (?, '', 'norm', 'now')",
            ("a" * 64,),
        )
        connection.commit()
    finally:
        connection.close()

    assert load_titles(db_path, ["a" * 64]) == {}


def test_load_titles_missing_database_returns_empty_without_creating_it(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)

    assert load_titles(db_path, ["a" * 64]) == {}
    assert db_path.exists() is False


def test_load_titles_missing_table_returns_empty(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)
    db_path.parent.mkdir(parents=True)
    sqlite3.connect(db_path).close()

    assert load_titles(db_path, ["a" * 64]) == {}


def test_load_titles_broken_database_returns_empty(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"not a database")

    assert load_titles(db_path, ["a" * 64]) == {}


def test_recent_titles_returns_newest_first_and_honours_limit(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)
    db_path.parent.mkdir(parents=True)
    for index, title in enumerate(("Первое название утра", "Второе название утра")):
        add_title(db_path, f"{index:064x}", title, created_at=f"2026-05-0{index + 1}")

    assert recent_titles(db_path) == ("Второе название утра", "Первое название утра")
    assert recent_titles(db_path, limit=1) == ("Второе название утра",)
    assert recent_titles(db_path, limit=0) == ()


def test_recent_titles_missing_or_broken_database_returns_empty(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)

    assert recent_titles(db_path) == ()

    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"not a database")
    assert recent_titles(db_path) == ()


def test_video_meta_includes_duration(tmp_path: Path) -> None:
    db_path = _db_path(tmp_path)
    add_downloaded_video(db_path, "a" * 64, "Описание", query="курятник")
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("UPDATE videos SET duration = 42")
        connection.commit()
    finally:
        connection.close()

    assert load_video_meta(db_path, ["a" * 64]) == {
        "a" * 64: VideoMeta(description="Описание", query="курятник", duration=42)
    }


# --- text -------------------------------------------------------------------


def test_clean_title_collapses_whitespace() -> None:
    assert clean_title("  Утренний \n\t свет  ") == "Утренний свет"


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("  ЁЖИК, Video!  ", "ежик video"),
        ("Утро ☀️ _кухня_", "утро кухня"),
        ("Свет — тишина…", "свет тишина"),
        ("A  B\nC", "a b c"),
    ],
)
def test_normalize_title_key_drops_punctuation_and_case(title: str, expected: str) -> None:
    assert normalize_title_key(title) == expected


@pytest.mark.parametrize("title", ["", "   ", "!!! ☀️", "___"])
def test_normalize_title_key_rejects_empty_result(title: str) -> None:
    with pytest.raises(ValueError, match="букв"):
        normalize_title_key(title)


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("  Утренний #тег  свет #ещё ", "утренний свет"),
        ("Ёжик, #тег!", "ежик"),
        ("Свет #один#два на кухне", "свет на кухне"),
        ("", ""),
        ("   ", ""),
        ("#тег #ещё", ""),
        ("!!!", ""),
    ],
)
def test_description_title_key_strips_hashtags(description: str, expected: str) -> None:
    assert description_title_key(description) == expected


@pytest.mark.parametrize(
    ("title", "language"),
    [
        ("Утренний свет на кухне", "ru"),
        ("Світло ранку на кухні", "uk"),
        ("Morning light in the kitchen", "en"),
        ("Morning light in the kitchen", "de"),
        ("Утренний свет, кухня — тишина!", "ru"),
        ("Уютная кухня и т. д. у окна", "ru"),
        ("Дом у А. С. Пушкина", "ru"),
        ("а" * 7 + " " + "б" * 7, "ru"),
        ("а" * 34 + " " + "б" * 35, "ru"),
    ],
)
def test_validate_title_accepts_good_titles(title: str, language: str) -> None:
    assert validate_title(title, language) is True


@pytest.mark.parametrize(
    ("title", "language"),
    [
        ("Свет утра", "ru"),
        ("а" * 34 + " " + "б" * 36, "ru"),
        ("Один", "ru"),
        ("Раз два три четыре пять шесть семь восемь", "ru"),
        ("Видео про утренний свет", "ru"),
        ("VIDEO about the morning light", "en"),
        ("Утренний свет #кухня", "ru"),
        ("Утренний свет @user", "ru"),
        ("Смотрите https://example.com", "ru"),
        ("Смотрите example.com сейчас", "ru"),
        ("Смотрите example.xyz сейчас", "ru"),
        ("Смотрите example.co.uk сейчас", "ru"),
        ("Смотрите bit.ly сейчас", "ru"),
        ('Утренний "свет" на кухне', "ru"),
        ("Утренний свет на кухне ☀", "ru"),
        ("Утренний_свет на кухне", "ru"),
        ("12345 67890 12345", "en"),
        ("Свет, , , , , ,", "ru"),
        ("Утренний ———————", "ru"),
        ("Morning light in the kitchen", "ru"),
        ("Утренний свет на кухне", "en"),
        ("Morning light's kitchen", "en"),
    ],
)
def test_validate_title_rejects_bad_titles(title: str, language: str) -> None:
    assert validate_title(title, language) is False


# --- frames -----------------------------------------------------------------


def test_frame_times_uses_percentages_or_fallback() -> None:
    assert frame_times(100) == (10.0, 50.0, 90.0)
    assert frame_times(3) == (0.3, 1.5, 2.7)
    assert frame_times(None) == (1.0, 3.0, 6.0)
    assert frame_times(0) == (1.0, 3.0, 6.0)


def _run_result(command: list[str], stdout: bytes, returncode: int = 0) -> Any:
    return subprocess.CompletedProcess(command, returncode, stdout=stdout, stderr=b"")


def test_extract_frames_calls_ffmpeg_per_moment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(command: list[str], **kwargs: Any) -> Any:
        calls.append((command, kwargs))
        return _run_result(command, f"jpeg-{len(calls)}".encode())

    monkeypatch.setattr(titles_frames.subprocess, "run", fake_run)
    monkeypatch.setattr(titles_frames.imageio_ffmpeg, "get_ffmpeg_exe", lambda: "/opt/ffmpeg")
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")

    result = extract_frames(video, 100)

    assert result == (b"jpeg-1", b"jpeg-2", b"jpeg-3")
    assert len(calls) == 3
    command, kwargs = calls[0]
    assert command[0] == "/opt/ffmpeg"
    assert command[1] == "-nostdin"
    assert str(video) in command
    assert "scale=768:768:force_original_aspect_ratio=decrease" in command
    assert command[command.index("-ss") + 1] == "10.000"
    assert command[command.index("-f") + 1] == "mp4"
    assert command[command.index("-protocol_whitelist") + 1] == "file"
    assert command.index("-nostdin") < command.index("-i")
    assert command.index("-f") < command.index("-i")
    assert command.index("-protocol_whitelist") < command.index("-i")
    assert command[command.index("-i") + 1] == str(video)
    assert kwargs["timeout"] == 30
    assert "shell" not in kwargs


def test_extract_frames_drops_empty_and_duplicate_frames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = [b"", b"same", b"same"]

    def fake_run(command: list[str], **kwargs: Any) -> Any:
        del kwargs
        return _run_result(command, outputs.pop(0))

    monkeypatch.setattr(titles_frames.subprocess, "run", fake_run)
    monkeypatch.setattr(titles_frames.imageio_ffmpeg, "get_ffmpeg_exe", lambda: "/opt/ffmpeg")

    assert extract_frames(tmp_path / "video.mp4", None) == (b"same",)


def test_extract_frames_skips_failed_frame(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = [(b"first", 0), (b"", 1), (b"third", 0)]

    def fake_run(command: list[str], **kwargs: Any) -> Any:
        del kwargs
        stdout, returncode = outputs.pop(0)
        return _run_result(command, stdout, returncode=returncode)

    monkeypatch.setattr(titles_frames.subprocess, "run", fake_run)
    monkeypatch.setattr(titles_frames.imageio_ffmpeg, "get_ffmpeg_exe", lambda: "/opt/ffmpeg")

    assert extract_frames(tmp_path / "video.mp4", None) == (b"first", b"third")


def test_extract_frames_wraps_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(command: list[str], **kwargs: Any) -> Any:
        raise subprocess.TimeoutExpired(command, 30)

    monkeypatch.setattr(titles_frames.subprocess, "run", fake_run)
    monkeypatch.setattr(titles_frames.imageio_ffmpeg, "get_ffmpeg_exe", lambda: "/opt/ffmpeg")

    with pytest.raises(TitleError, match="кадры"):
        extract_frames(tmp_path / "video.mp4", None)


def test_extract_frames_wraps_os_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(command: list[str], **kwargs: Any) -> Any:
        raise OSError("no ffmpeg")

    monkeypatch.setattr(titles_frames.subprocess, "run", fake_run)
    monkeypatch.setattr(titles_frames.imageio_ffmpeg, "get_ffmpeg_exe", lambda: "/opt/ffmpeg")

    with pytest.raises(TitleError, match="кадры"):
        extract_frames(tmp_path / "video.mp4", None)


def test_extract_frames_wraps_missing_ffmpeg(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing_ffmpeg() -> str:
        raise RuntimeError("ffmpeg not found")

    monkeypatch.setattr(titles_frames.imageio_ffmpeg, "get_ffmpeg_exe", missing_ffmpeg)

    with pytest.raises(TitleError, match="кадры"):
        extract_frames(tmp_path / "video.mp4", None)


# --- client -----------------------------------------------------------------


def test_generate_titles_sends_frames_language_and_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(titles_client, "_sleep", lambda _: None)
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers.get("Authorization")
        seen["payload"] = json.loads(request.read())
        return httpx.Response(200, json=_completion("Утренний свет на кухне"))

    with _http_client(httpx.MockTransport(handler)) as http_client:
        titles = generate_titles(
            [b"one", b"two", b"three"],
            "ru",
            "д" * 600,
            "к" * 600,
            ["Старое название"],
            _settings(reasoning_effort="high"),
            client=http_client,
        )

    assert titles == ("Утренний свет на кухне",)
    assert seen["url"] == "https://ollama.com/v1/chat/completions"
    assert seen["authorization"] == f"Bearer {_SECRET}"
    payload = seen["payload"]
    assert payload["model"] == "deepseek-v4.1-flash"
    assert payload["temperature"] == 0.9
    assert payload["max_tokens"] == 64000
    assert payload["reasoning_effort"] == "high"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["stream"] is False
    system = payload["messages"][0]["content"]
    user_content = payload["messages"][1]["content"]
    user_text = user_content[0]["text"]
    assert "Russian (ru)" in system
    assert "- Старое название" in system
    assert "Here are three frames." in user_text
    assert "Video language: ru (Russian)" in user_text
    assert '"' + "д" * 500 + '"' in user_text
    assert "д" * 501 not in user_text
    assert "к" * 501 not in user_text
    images = [item for item in user_content if item["type"] == "image_url"]
    assert len(images) == 3
    assert images[0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_generate_titles_omits_authorization_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(titles_client, "_sleep", lambda _: None)
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization")
        return httpx.Response(200, json=_completion("Утренний свет на кухне"))

    with _http_client(httpx.MockTransport(handler)) as http_client:
        generate_titles(
            [b"one"],
            "ru",
            "",
            "",
            [],
            _settings(api_key=None),
            client=http_client,
        )

    assert seen["authorization"] is None


def test_generate_titles_rejects_malformed_api_key() -> None:
    secret = "sk-SECRET\nVALUE"
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=_completion("Утренний свет на кухне"))

    with (
        _http_client(httpx.MockTransport(handler)) as http_client,
        pytest.raises(TitleClientError, match="ключ") as error,
    ):
        generate_titles(
            [b"one"],
            "ru",
            "",
            "",
            [],
            _settings(api_key=SecretStr(secret)),
            client=http_client,
        )

    assert called is False
    assert secret not in str(error.value)
    assert secret not in repr(error.value)
    chain: BaseException | None = error.value
    while chain is not None:
        assert secret not in str(chain)
        assert secret not in repr(chain)
        chain = chain.__cause__ or chain.__context__


def test_generate_titles_wraps_local_protocol_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(titles_client, "_sleep", lambda _: None)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.LocalProtocolError("bad header value", request=request)

    with (
        _http_client(httpx.MockTransport(handler)) as http_client,
        pytest.raises(TitleClientError, match="запрос") as error,
    ):
        generate_titles([b"one"], "ru", "", "", [], _settings(), client=http_client)

    assert calls == 1
    assert error.value.__cause__ is None


def test_generate_titles_uses_default_client_with_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(titles_client, "_sleep", lambda _: None)
    seen: dict[str, Any] = {}

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion("Утренний свет на кухне"))

    http_client = httpx.Client(transport=httpx.MockTransport(handler))

    def fake_client(**kwargs: Any) -> httpx.Client:
        seen.update(kwargs)
        return http_client

    monkeypatch.setattr(titles_client.httpx, "Client", fake_client)

    titles = generate_titles([b"one"], "ru", "", "", [], _settings())

    assert titles == ("Утренний свет на кухне",)
    assert seen["timeout"] == 60


def test_generate_titles_retries_server_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(titles_client, "_sleep", lambda _: None)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500)
        return httpx.Response(200, json=_completion("Утренний свет на кухне"))

    with _http_client(httpx.MockTransport(handler)) as http_client:
        titles = generate_titles([b"one"], "ru", "", "", [], _settings(), client=http_client)

    assert calls == 2
    assert titles == ("Утренний свет на кухне",)


def test_generate_titles_retries_rate_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(titles_client, "_sleep", lambda _: None)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429)
        return httpx.Response(200, json=_completion("Утренний свет на кухне"))

    with _http_client(httpx.MockTransport(handler)) as http_client:
        generate_titles([b"one"], "ru", "", "", [], _settings(), client=http_client)

    assert calls == 2


def test_generate_titles_retries_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(titles_client, "_sleep", lambda _: None)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json=_completion("Утренний свет на кухне"))

    with _http_client(httpx.MockTransport(handler)) as http_client:
        generate_titles([b"one"], "ru", "", "", [], _settings(), client=http_client)

    assert calls == 2


def test_generate_titles_wraps_invalid_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(titles_client, "_sleep", lambda _: None)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        del request
        raise httpx.InvalidURL("bad url")

    with (
        _http_client(httpx.MockTransport(handler)) as http_client,
        pytest.raises(TitleClientError, match="адрес"),
    ):
        generate_titles([b"one"], "ru", "", "", [], _settings(), client=http_client)

    assert calls == 1


def test_generate_titles_gives_up_after_three_transient_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(titles_client, "_sleep", lambda _: None)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    with (
        _http_client(httpx.MockTransport(handler)) as http_client,
        pytest.raises(TransientTitleError, match="код 503"),
    ):
        generate_titles([b"one"], "ru", "", "", [], _settings(), client=http_client)

    assert calls == 3


@pytest.mark.parametrize("status", [400, 401, 403, 422])
def test_generate_titles_does_not_retry_client_error(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
) -> None:
    monkeypatch.setattr(titles_client, "_sleep", lambda _: None)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(status, text="secret prompt body")

    with (
        _http_client(httpx.MockTransport(handler)) as http_client,
        pytest.raises(TitleClientError, match=f"код {status}") as error,
    ):
        generate_titles([b"one"], "ru", "", "", [], _settings(), client=http_client)

    assert calls == 1
    assert _SECRET not in str(error.value)
    assert "secret prompt body" not in str(error.value)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": "not json"}}]},
        {"choices": [{"message": {"content": "[]"}}]},
        {"choices": [{"message": {"content": '{"names": []}'}}]},
        {"choices": [{"message": {}}]},
        {"choices": ["bad"]},
        {"choices": [{"message": "bad"}]},
        [],
    ],
)
def test_generate_titles_rejects_bad_response(body: dict[str, Any]) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    with (
        _http_client(httpx.MockTransport(handler)) as http_client,
        pytest.raises(TitleClientError),
    ):
        generate_titles([b"one"], "ru", "", "", [], _settings(), client=http_client)


def test_generate_titles_rejects_non_json_response() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    with (
        _http_client(httpx.MockTransport(handler)) as http_client,
        pytest.raises(TitleClientError, match="не JSON"),
    ):
        generate_titles([b"one"], "ru", "", "", [], _settings(), client=http_client)


def test_generate_titles_does_not_log_secret_or_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(titles_client, "_sleep", lambda _: None)
    messages: list[str] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="secret response body")

    sink = logger.add(lambda message: messages.append(str(message)), level="DEBUG")
    try:
        with (
            _http_client(httpx.MockTransport(handler)) as http_client,
            pytest.raises(TitleClientError),
        ):
            generate_titles(
                [b"one"],
                "ru",
                "описание",
                "запрос",
                [],
                _settings(),
                client=http_client,
            )
    finally:
        logger.remove(sink)

    logged = "\n".join(messages)
    assert _SECRET not in logged
    assert "secret response body" not in logged
    assert "Author description" not in logged


def test_generate_titles_requires_frames() -> None:
    with pytest.raises(TitleError, match="кадров"):
        generate_titles([], "ru", "", "", [], _settings())


# --- settings ---------------------------------------------------------------


def test_titles_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "TITLES_API_URL",
        "TITLES_API_KEY",
        "TITLES_MODEL",
        "TITLES_TIMEOUT_SECONDS",
        "TITLES_REASONING_EFFORT",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = load_titles_settings()

    assert settings.api_url == "https://ollama.com/v1"
    assert settings.api_key is None
    assert settings.model == "deepseek-v4.1-flash"
    assert settings.timeout_seconds == 60
    assert settings.reasoning_effort == "none"


def test_titles_settings_blank_key_becomes_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TITLES_API_KEY", "   ")

    assert TitlesSettings().api_key is None


def test_titles_settings_hides_secret_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TITLES_API_KEY", _SECRET)

    settings = TitlesSettings()

    assert _SECRET not in repr(settings)
    assert settings.api_key is not None


@pytest.mark.parametrize(
    "url",
    [
        "ftp://ollama.com/v1",
        "https://ollama.com/v1/",
        "https://ollama.com /v1",
        "ollama.com/v1",
        "https://ollama.com:bad/v1",
    ],
)
def test_titles_settings_rejects_bad_url(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    monkeypatch.setenv("TITLES_API_URL", url)

    with pytest.raises(ConfigError, match="настройки названий"):
        load_titles_settings()


@pytest.mark.parametrize("value", ["4", "301"])
def test_titles_settings_rejects_timeout_outside_range(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("TITLES_TIMEOUT_SECONDS", value)

    with pytest.raises(ConfigError, match="TITLES_TIMEOUT_SECONDS"):
        load_titles_settings()


def test_titles_settings_rejects_unknown_reasoning_effort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TITLES_REASONING_EFFORT", "extreme")

    with pytest.raises(ConfigError, match="TITLES_REASONING_EFFORT"):
        load_titles_settings()


def test_titles_settings_error_does_not_repeat_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TITLES_API_KEY", _SECRET)
    monkeypatch.setenv("TITLES_TIMEOUT_SECONDS", "1")

    with pytest.raises(ConfigError) as error:
        load_titles_settings()

    assert _SECRET not in str(error.value)


# --- assign_titles ----------------------------------------------------------


def _patch_generation(
    monkeypatch: pytest.MonkeyPatch,
    *,
    variants: dict[str, tuple[str, ...]] | None = None,
    errors: frozenset[str] = frozenset(),
    calls: list[dict[str, Any]] | None = None,
) -> None:
    def fake_extract(path: Path, duration: int | None) -> tuple[bytes, ...]:
        return (path.stem.encode(),)

    def fake_generate(
        frames: tuple[bytes, ...],
        language: str,
        description: str,
        query: str,
        recent: list[str],
        settings: TitlesSettings,
    ) -> tuple[str, ...]:
        del settings
        tag = frames[0].decode()
        if calls is not None:
            calls.append(
                {
                    "language": language,
                    "description": description,
                    "query": query,
                    "recent": list(recent),
                    "tag": tag,
                }
            )
        if tag in errors:
            raise TitleError("boom")
        if variants is not None:
            return variants[tag]
        if language in {"ru", "uk", "kk", "bg", "sr", "mk", "be"}:
            return (f"Свет кадра {tag[:6]}",)
        return (f"Morning light {tag[:6]}",)

    monkeypatch.setattr(titles_service, "extract_frames", fake_extract)
    monkeypatch.setattr(titles_service, "generate_titles", fake_generate)


def _stock(tmp_path: Path, language: str, *hashes: str) -> list[Path]:
    return [touch_mp4(tmp_path / CONTENT_DIR / language / f"{sha256}.mp4") for sha256 in hashes]


def test_assign_titles_saves_two_titles_and_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_generation(monkeypatch)
    _stock(tmp_path, "ru", "a" * 64, "b" * 64)
    db_path = _db_path(tmp_path)

    first = assign_titles(tmp_path, _settings(), now=_NOW)

    assert first == TitlePassResult(candidates=2, titled=2, skipped=0)
    stored = load_titles(db_path, ["a" * 64, "b" * 64])
    assert set(stored) == {"a" * 64, "b" * 64}
    assert stored["a" * 64] != stored["b" * 64]

    second = assign_titles(tmp_path, _settings(), now=_NOW)

    assert second == TitlePassResult(candidates=0, titled=0, skipped=0)
    assert load_titles(db_path, ["a" * 64, "b" * 64]) == stored


def test_assign_titles_uses_language_directory_and_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    _patch_generation(monkeypatch, calls=calls)
    sha256 = "a" * 64
    _stock(tmp_path, "en", sha256)
    db_path = _db_path(tmp_path)
    add_downloaded_video(db_path, sha256, "Описание", query="курятник")
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("UPDATE videos SET duration = 120")
        connection.commit()
    finally:
        connection.close()
    durations: list[int | None] = []

    def fake_extract(path: Path, duration: int | None) -> tuple[bytes, ...]:
        del path
        durations.append(duration)
        return (sha256.encode(),)

    monkeypatch.setattr(titles_service, "extract_frames", fake_extract)

    result = assign_titles(tmp_path, _settings(), now=_NOW)

    assert result == TitlePassResult(candidates=1, titled=1, skipped=0)
    assert durations == [120]
    assert calls[0]["language"] == "en"
    assert calls[0]["description"] == "Описание"
    assert calls[0]["query"] == "курятник"


def test_assign_titles_titles_orphan_mp4_without_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    _patch_generation(monkeypatch, calls=calls)
    sha256 = "a" * 64
    _stock(tmp_path, "ru", sha256)
    durations: list[int | None] = []

    def fake_extract(path: Path, duration: int | None) -> tuple[bytes, ...]:
        del path
        durations.append(duration)
        return (sha256.encode(),)

    monkeypatch.setattr(titles_service, "extract_frames", fake_extract)

    result = assign_titles(tmp_path, _settings(), now=_NOW)

    assert result == TitlePassResult(candidates=1, titled=1, skipped=0)
    assert durations == [None]
    assert calls[0]["description"] == ""
    assert calls[0]["query"] == ""


def test_assign_titles_takes_first_free_valid_variant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sha256 = "a" * 64
    _patch_generation(
        monkeypatch,
        variants={sha256: ("Коротко", "Свет кадра aaaaaa")},
    )
    _stock(tmp_path, "ru", sha256)

    result = assign_titles(tmp_path, _settings(), now=_NOW)

    assert result == TitlePassResult(candidates=1, titled=1, skipped=0)
    assert load_titles(_db_path(tmp_path), [sha256]) == {sha256: "Свет кадра aaaaaa"}


def test_assign_titles_skips_title_equal_to_description(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sha256 = "a" * 64
    _patch_generation(monkeypatch, variants={sha256: ("Утренний свет на кухне",)})
    _stock(tmp_path, "ru", sha256)
    add_downloaded_video(
        _db_path(tmp_path),
        sha256,
        "Утренний свет на кухне #кухня",
    )

    result = assign_titles(tmp_path, _settings(), now=_NOW)

    assert result == TitlePassResult(candidates=1, titled=0, skipped=1)
    assert load_titles(_db_path(tmp_path), [sha256]) == {}


def test_assign_titles_skips_normalized_description_match_and_tries_next(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sha256 = "a" * 64
    _patch_generation(
        monkeypatch,
        variants={sha256: ("УТРЕННИЙ СВЕТ, НА КУХНЕ!", "Тихий вечер у окна")},
    )
    _stock(tmp_path, "ru", sha256)
    add_downloaded_video(
        _db_path(tmp_path),
        sha256,
        "Утренний свет на кухне #кухня",
    )

    result = assign_titles(tmp_path, _settings(), now=_NOW)

    assert result == TitlePassResult(candidates=1, titled=1, skipped=0)
    assert load_titles(_db_path(tmp_path), [sha256]) == {sha256: "Тихий вечер у окна"}


def test_assign_titles_saves_title_different_from_description(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sha256 = "a" * 64
    _patch_generation(monkeypatch, variants={sha256: ("Тихий вечер у окна",)})
    _stock(tmp_path, "ru", sha256)
    add_downloaded_video(
        _db_path(tmp_path),
        sha256,
        "Утренний свет на кухне #кухня",
    )

    result = assign_titles(tmp_path, _settings(), now=_NOW)

    assert result == TitlePassResult(candidates=1, titled=1, skipped=0)
    assert load_titles(_db_path(tmp_path), [sha256]) == {sha256: "Тихий вечер у окна"}


def test_assign_titles_moves_to_next_variant_on_norm_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sha256 = "a" * 64
    db_path = _db_path(tmp_path)
    add_title(db_path, "c" * 64, "Свет кадра aaaaaa")
    _patch_generation(
        monkeypatch,
        variants={sha256: ("Свет кадра aaaaaa", "Новое название утра")},
    )
    _stock(tmp_path, "ru", sha256)

    result = assign_titles(tmp_path, _settings(), now=_NOW)

    assert result == TitlePassResult(candidates=1, titled=1, skipped=0)
    assert load_titles(db_path, [sha256]) == {sha256: "Новое название утра"}


def test_assign_titles_skips_when_every_variant_is_taken(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sha256 = "a" * 64
    db_path = _db_path(tmp_path)
    add_title(db_path, "c" * 64, "Свет кадра aaaaaa")
    _patch_generation(monkeypatch, variants={sha256: ("Свет кадра aaaaaa",)})
    file_path = _stock(tmp_path, "ru", sha256)[0]

    result = assign_titles(tmp_path, _settings(), now=_NOW)

    assert result == TitlePassResult(candidates=1, titled=0, skipped=1)
    assert load_titles(db_path, [sha256]) == {}
    assert file_path.exists() is True


def test_assign_titles_skips_file_without_frames(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stock(tmp_path, "ru", "a" * 64)
    called = False

    def fake_extract(path: Path, duration: int | None) -> tuple[bytes, ...]:
        del path, duration
        return ()

    def fake_generate(*args: object, **kwargs: object) -> tuple[str, ...]:
        nonlocal called
        called = True
        return ()

    monkeypatch.setattr(titles_service, "extract_frames", fake_extract)
    monkeypatch.setattr(titles_service, "generate_titles", fake_generate)

    result = assign_titles(tmp_path, _settings(), now=_NOW)

    assert result == TitlePassResult(candidates=1, titled=0, skipped=1)
    assert called is False


def test_assign_titles_continues_after_one_file_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_generation(monkeypatch, errors=frozenset({"a" * 64}))
    _stock(tmp_path, "ru", "a" * 64, "b" * 64)

    result = assign_titles(tmp_path, _settings(), now=_NOW)

    assert result == TitlePassResult(candidates=2, titled=1, skipped=1)
    assert load_titles(_db_path(tmp_path), ["a" * 64, "b" * 64]) == {"b" * 64: "Свет кадра bbbbbb"}


def test_assign_titles_skips_unnormalizable_option(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    log_records: list[dict[str, Any]],
) -> None:
    sha256 = "a" * 64
    _patch_generation(monkeypatch, variants={sha256: ("Свет кадра aaaaaa",)})
    _stock(tmp_path, "ru", sha256)

    def broken_key(title: str) -> str:
        raise ValueError(title)

    monkeypatch.setattr(titles_service, "normalize_title_key", broken_key)

    result = assign_titles(tmp_path, _settings(), now=_NOW)

    assert result == TitlePassResult(candidates=1, titled=0, skipped=1)
    assert load_titles(_db_path(tmp_path), [sha256]) == {}
    records = [record for record in log_records if record["function"] == "_save_candidate"]
    assert [record["level"].name for record in records] == ["DEBUG"]
    assert records[0]["message"] == (f"Вариант названия без букв и цифр пропущен: {sha256[:8]}")
    assert records[0]["extra"] == {"sha256": sha256, "error_type": "ValueError"}


def test_assign_titles_ignores_foreign_files_and_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_generation(monkeypatch)
    content_dir = tmp_path / CONTENT_DIR
    content_dir.mkdir()
    (content_dir / "notes.txt").write_text("not a video")
    (content_dir / "long-name").mkdir()
    (content_dir / "long-name" / "video.mp4").write_bytes(b"video")
    (content_dir / "ru").mkdir()
    (content_dir / "ru" / "readme.txt").write_text("not a video")
    _stock(tmp_path, "ru", "a" * 64)

    result = assign_titles(tmp_path, _settings(), now=_NOW)

    assert result == TitlePassResult(candidates=1, titled=1, skipped=0)


def test_assign_titles_updates_recent_titles_during_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []
    _patch_generation(monkeypatch, calls=calls)
    _stock(tmp_path, "ru", "a" * 64, "b" * 64)

    assign_titles(tmp_path, _settings(), now=_NOW)

    assert calls[0]["recent"] == []
    assert calls[1]["recent"] == ["Свет кадра aaaaaa"]


def test_assign_titles_does_not_swallow_database_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_generation(monkeypatch)
    _stock(tmp_path, "ru", "a" * 64)

    def broken_save(*args: object, **kwargs: object) -> bool:
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(titles_service, "save_title", broken_save)

    with pytest.raises(sqlite3.OperationalError, match="disk I/O"):
        assign_titles(tmp_path, _settings(), now=_NOW)


def test_assign_titles_creates_schema_on_empty_content(tmp_path: Path) -> None:
    result = assign_titles(tmp_path, _settings(), now=_NOW)

    assert result == TitlePassResult(candidates=0, titled=0, skipped=0)
    connection = sqlite3.connect(_db_path(tmp_path))
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'titles'"
        ).fetchall()
    finally:
        connection.close()
    assert rows == [("titles",)]
    assert load_titles(_db_path(tmp_path), ["a" * 64]) == {}


# --- flow -------------------------------------------------------------------


def test_titles_flow_returns_disabled_without_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(titles_flow, "load_settings", lambda: _simple_settings())
    monkeypatch.setattr(
        titles_flow,
        "load_titles_settings",
        lambda: TitlesSettings(api_key=None),
    )
    called = False

    def fake_assign(*args: object, **kwargs: object) -> TitlePassResult:
        nonlocal called
        called = True
        return TitlePassResult(candidates=0, titled=0, skipped=0)

    monkeypatch.setattr(titles_flow, "assign_titles", fake_assign)
    messages: list[str] = []
    sink = logger.add(lambda message: messages.append(str(message)), level="ERROR")
    try:
        assert titles_flow.titles.fn() == "disabled"
    finally:
        logger.remove(sink)
    assert called is False
    assert any("titles: TITLES_API_KEY не задан" in message for message in messages)


def test_titles_flow_returns_result(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _simple_settings()
    seen: dict[str, object] = {}

    def fake_assign(data_dir: Path, titles_settings: TitlesSettings) -> TitlePassResult:
        seen["data_dir"] = data_dir
        seen["settings"] = titles_settings
        return TitlePassResult(candidates=3, titled=2, skipped=1)

    monkeypatch.setattr(titles_flow, "load_settings", lambda: settings)
    monkeypatch.setattr(titles_flow, "load_titles_settings", lambda: _settings())
    monkeypatch.setattr(titles_flow, "assign_titles", fake_assign)

    assert titles_flow.titles.fn() == "titled=2 skipped=1"
    assert seen["data_dir"] == settings.data_dir


def _simple_settings() -> Any:
    return type("Settings", (), {"data_dir": Path("/data")})()
