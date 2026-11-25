"""Text client: OpenAI-compatible request, key handling and answer parsing."""

from __future__ import annotations

import json
import random
from typing import Any

import httpx
import pytest

from content_factory.config.settings import GeneratorSettings
from content_factory.exceptions.generator import TextError
from content_factory.services.generator import texts
from content_factory.services.generator.texts import (
    TextClient,
    _bearer,
    _content,
    _message_content,
    _payload,
    _spin,
)


def _settings(**kwargs: Any) -> GeneratorSettings:
    values: dict[str, Any] = {"OPENAI_BASE_URL": "http://api.test/v1", "OPENAI_API_KEY": "sk-123"}
    values.update(kwargs)
    return GeneratorSettings(**values)


def _chat_response(
    content: Any = "<result><block>Текст карточки</block></result>",
) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": content}}]},
        request=httpx.Request("POST", "http://api.test/v1/chat/completions"),
    )


def test_client_posts_system_prompt_and_returns_content() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["auth"] = request.headers.get("Authorization")
        seen["body"] = request.content
        return _chat_response()

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        answer = TextClient(_settings(), client=client).write("Промпт профиля")

    assert answer.startswith("<result>")
    assert seen["path"] == "/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-123"
    assert b'"model":"deepseek-v4.1-flash"' in seen["body"]
    assert b'"messages"' in seen["body"]
    assert b'"system"' in seen["body"]


def test_client_sends_the_custom_user_message() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return _chat_response('{"title": "Профиль", "summary": "Короткое пояснение."}')

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        TextClient(_settings(), client=client).write("Системный промпт", "Опиши профиль")

    messages = seen["body"]["messages"]
    assert messages[0] == {"role": "system", "content": "Системный промпт"}
    assert messages[1] == {"role": "user", "content": "Опиши профиль"}


def test_client_without_key_sends_no_authorization() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        return _chat_response()

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        TextClient(_settings(OPENAI_API_KEY=None), client=client).write("prompt")

    assert seen["auth"] is None


def test_client_creates_its_own_http_client(monkeypatch) -> None:
    calls: list[str] = []

    class _FakeClient:
        def __init__(self, timeout: float) -> None:
            calls.append(f"client:{timeout}")

        def __enter__(self) -> _FakeClient:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def post(self, url: str, **kwargs: Any) -> httpx.Response:
            calls.append(url)
            return _chat_response()

    monkeypatch.setattr(texts.httpx, "Client", _FakeClient)

    assert TextClient(_settings(), timeout=5).write("prompt").startswith("<result>")
    assert calls[0] == "client:5"
    assert calls[1] == "http://api.test/v1/chat/completions"


def test_client_maps_http_errors_to_text_errors() -> None:
    def reject(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="denied", request=request)

    with (
        httpx.Client(transport=httpx.MockTransport(reject)) as client,
        pytest.raises(TextError, match="код 401"),
    ):
        TextClient(_settings(), client=client).write("prompt")


def test_client_maps_transport_errors_and_invalid_urls() -> None:
    class _Broken:
        def post(self, *args: Any, **kwargs: Any) -> httpx.Response:
            raise httpx.ConnectError("no route")

    with pytest.raises(TextError, match="недоступна"):
        TextClient(_settings(), client=_Broken()).write("prompt")  # type: ignore[arg-type]

    class _Timeout:
        def post(self, *args: Any, **kwargs: Any) -> httpx.Response:
            raise httpx.ReadTimeout("slow")

    with pytest.raises(TextError, match="недоступна"):
        TextClient(_settings(), client=_Timeout()).write("prompt")  # type: ignore[arg-type]

    class _BadUrl:
        def post(self, *args: Any, **kwargs: Any) -> httpx.Response:
            raise httpx.InvalidURL("bad url")

    with pytest.raises(TextError, match="неверный адрес"):
        TextClient(_settings(), client=_BadUrl()).write("prompt")  # type: ignore[arg-type]


def test_client_rejects_a_malformed_key_before_the_request() -> None:
    client = TextClient(_settings(OPENAI_API_KEY="sk-\nbroken"))

    with pytest.raises(TextError, match="недопустимые символы"):
        client.write("prompt")


def test_bearer_allows_printable_keys_only() -> None:
    assert _bearer("sk-123") == "Bearer sk-123"

    with pytest.raises(TextError, match="недопустимые символы"):
        _bearer("sk-тест")


def test_content_requires_json_object_text() -> None:
    with pytest.raises(TextError, match="не JSON"):
        _content(httpx.Response(200, content=b"not json", request=httpx.Request("GET", "http://x")))

    with pytest.raises(TextError, match="не объект"):
        _message_content([])

    with pytest.raises(TextError, match="choices"):
        _message_content({})

    with pytest.raises(TextError, match="choices"):
        _message_content({"choices": []})

    with pytest.raises(TextError, match="не вернула текст"):
        _message_content({"choices": [{"message": {"content": 5}}]})

    with pytest.raises(TextError, match="не вернула текст"):
        _message_content({"choices": [{"no_message": True}]})

    with pytest.raises(TextError, match="пустой ответ"):
        _content(_chat_response("   "))


def test_payload_truncates_absurd_prompts() -> None:
    payload = _payload("x" * 21_000, "model")

    assert payload["model"] == "model"
    assert len(payload["messages"][0]["content"]) == 20_000
    assert payload["messages"][1]["content"] == "Сгенерируй один текст для одного видео."
    assert payload["stream"] is False


def test_spin_expands_only_option_groups() -> None:
    text = _spin("{a|b} and {single} and {c|d|e}", random.Random(0))

    assert "{a|b}" not in text
    assert "{single}" in text
    assert text.count(" and ") == 2
