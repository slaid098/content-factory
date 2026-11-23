"""DeepSeek client that turns video frames into title options."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Sequence
from typing import Any

import httpx
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from content_factory.config.settings import TitlesSettings
from content_factory.exceptions.titles import (
    TitleClientError,
    TitleError,
    TransientTitleError,
)
from content_factory.services.titles.languages import language_name

_ATTEMPTS = 3
_MAX_DATA_LENGTH = 500
_TITLES_IN_RESPONSE = 3
_PRINTABLE_KEY_RANGE = range(33, 127)


def generate_titles(
    frames: Sequence[bytes],
    language: str,
    description: str,
    query: str,
    recent: Sequence[str],
    settings: TitlesSettings,
    *,
    client: httpx.Client | None = None,
) -> tuple[str, ...]:
    """Ask the title API for three options based on video frames.

    Transport failures, timeouts, HTTP 429 and HTTP 5xx are retried three
    times with an exponential pause. Other HTTP errors fail immediately. The
    API key, the prompt and the response body are never logged.

    Args:
        frames: JPEG frames of the video, at least one.
        language: Two-letter language code of the content directory.
        description: Author description used as untrusted data.
        query: Search query that found the video, used as untrusted data.
        recent: Titles already stored in the network, newest first.
        settings: Title API settings.
        client: Optional HTTP client, used by tests to avoid real network.

    Returns:
        Raw title options returned by the API, in their original order.

    Raises:
        TitleError: No frames were supplied.
        TitleClientError: The API rejected the request or returned bad JSON.
        TransientTitleError: The API stayed unavailable after every attempt.
    """
    if not frames:
        raise TitleError("нет кадров для названия")
    retrying = _retrying()
    for attempt in retrying:
        with attempt:
            return _request(frames, language, description, query, recent, settings, client)
    raise TitleClientError("не удалось получить названия")  # pragma: no cover


def _retrying() -> Retrying:
    """Build the retry policy of one generate call."""
    return Retrying(
        stop=stop_after_attempt(_ATTEMPTS),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception_type(TransientTitleError),
        reraise=True,
        sleep=_sleep,
    )


def _sleep(seconds: float) -> None:
    """Pause between retries; tests replace it to stay fast."""
    time.sleep(seconds)


def _request(
    frames: Sequence[bytes],
    language: str,
    description: str,
    query: str,
    recent: Sequence[str],
    settings: TitlesSettings,
    client: httpx.Client | None,
) -> tuple[str, ...]:
    """Send one chat-completions request and parse its title list."""
    authorization = _authorization(settings)
    payload = _payload(frames, language, description, query, recent, settings)
    headers = {"Content-Type": "application/json"}
    if authorization is not None:
        headers["Authorization"] = authorization
    url = f"{settings.api_url.rstrip('/')}/chat/completions"
    try:
        if client is None:
            with httpx.Client(timeout=settings.timeout_seconds) as http:
                response = http.post(url, json=payload, headers=headers)
        else:
            response = client.post(
                url,
                json=payload,
                headers=headers,
                timeout=settings.timeout_seconds,
            )
    except httpx.LocalProtocolError:
        raise TitleClientError("не удалось отправить запрос к API названий") from None
    except (httpx.TransportError, httpx.TimeoutException) as ex:
        raise TransientTitleError("API названий недоступен или не ответил вовремя") from ex
    except httpx.InvalidURL as ex:
        raise TitleClientError("неверный адрес API названий") from ex
    _check_status(response)
    return _parse_response(response)


def _authorization(settings: TitlesSettings) -> str | None:
    """Build the Authorization header without ever exposing a malformed key.

    The header value must be printable ASCII without whitespace; other
    characters make httpx raise an error that embeds the raw header.

    Args:
        settings: Title API settings.

    Returns:
        The header value, or ``None`` when no key is configured.

    Raises:
        TitleClientError: The key has a character outside printable ASCII 33..126.
    """
    if settings.api_key is None:
        return None
    key = settings.api_key.get_secret_value()
    if any(ord(char) not in _PRINTABLE_KEY_RANGE for char in key):
        raise TitleClientError("ключ API названий содержит недопустимые символы")
    return f"Bearer {key}"


def _payload(
    frames: Sequence[bytes],
    language: str,
    description: str,
    query: str,
    recent: Sequence[str],
    settings: TitlesSettings,
) -> dict[str, Any]:
    """Build the OpenAI-compatible chat-completions payload."""
    content: list[dict[str, Any]] = [
        {"type": "text", "text": _user_text(language, description, query)},
    ]
    for frame in frames:
        encoded = base64.b64encode(frame).decode("ascii")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
            }
        )
    return {
        "model": settings.model,
        "messages": [
            {"role": "system", "content": _system_prompt(language, recent)},
            {"role": "user", "content": content},
        ],
        "temperature": 0.9,
        "max_tokens": 64000,
        "response_format": {"type": "json_object"},
        "reasoning_effort": settings.reasoning_effort,
        "stream": False,
    }


def _system_prompt(language: str, recent: Sequence[str]) -> str:
    """Build the English system prompt with the language and recent titles."""
    name = language_name(language)
    recent_text = (
        "\n".join(f"- {title}" for title in recent)
        if recent
        else "- There are no recent titles yet."
    )
    return f"""You write short titles for short vertical videos.
Return exactly {_TITLES_IN_RESPONSE} different title options as a JSON object:
{{"titles": ["...", "...", "..."]}}.
Rules:
- Each title must describe something visible in the frames.
- Use 2 to 7 words and 15 to 70 characters.
- Write the titles in {name} ({language}).
- Make the options different in kind: one says what happens, one names a mood or
  a detail, one is a short lively phrase.
- Do not start every option with the same word.
- Do not invent places, names, brands or events that are not visible.
- Do not use the words "видео", "video", "ролик", "shorts", "клип" or "clip".
- Do not use hashtags, links, @mentions, emojis or quotation marks.
- Use only letters, digits, spaces and the punctuation .,!?—:-…
- Do not repeat or closely paraphrase these recent titles:
{recent_text}
Return JSON only, with no explanation."""


def _user_text(language: str, description: str, query: str) -> str:
    """Build the user message with the language and author data."""
    return (
        f"Video language: {language} ({language_name(language)})\n"
        f"Author description: {json.dumps(_clip(description), ensure_ascii=False)}\n"
        f"Search query: {json.dumps(_clip(query), ensure_ascii=False)}\n"
        "Here are three frames."
    )


def _clip(text: str) -> str:
    """Limit untrusted prompt data to 500 characters."""
    return text[:_MAX_DATA_LENGTH]


def _check_status(response: httpx.Response) -> None:
    """Raise a retryable or permanent error for an unsuccessful response."""
    if response.is_success:
        return
    status = response.status_code
    if status == 429 or status >= 500:
        raise TransientTitleError(f"API названий временно недоступен (код {status})")
    raise TitleClientError(f"API названий вернул ошибку (код {status})")


def _parse_response(response: httpx.Response) -> tuple[str, ...]:
    """Parse the strict ``{"titles": [...]}`` object from the chat response."""
    try:
        payload = response.json()
    except ValueError as ex:
        raise TitleClientError("API названий вернул не JSON") from ex
    content = _message_content(payload)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as ex:
        raise TitleClientError("API названий вернул битый JSON") from ex
    if not isinstance(parsed, dict):
        raise TitleClientError("API названий вернул не объект")
    titles = parsed.get("titles")
    if not isinstance(titles, list):
        raise TitleClientError("API названий не вернул список titles")
    return tuple(title for title in titles if isinstance(title, str))


def _message_content(payload: object) -> str:
    """Extract the non-empty message content from a chat-completions body."""
    if not isinstance(payload, dict):
        raise TitleClientError("API названий вернул не объект")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise TitleClientError("API названий не вернул choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise TitleClientError("API названий вернул неверный choice")
    message = first.get("message")
    if not isinstance(message, dict):
        raise TitleClientError("API названий не вернул сообщение")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise TitleClientError("API названий вернул пустой ответ")
    return content
