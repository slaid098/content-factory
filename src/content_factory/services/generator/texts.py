"""Text step: ask the model for a two-block card and parse its answer."""

from __future__ import annotations

import json
import random
import re
import time
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
from loguru import logger

from content_factory.config.settings import GeneratorSettings
from content_factory.exceptions.generator import TextError
from content_factory.services.generator.auth import bearer_header
from content_factory.services.generator.profiles import _Profile, text_prompt

_TEXT_ATTEMPTS = 3
_TEXT_PAUSE = 2.0
_REQUEST_TIMEOUT = 120.0
# Профильный промпт — доверенный текст с жёстким форматом ответа, его нельзя
# обрезать: хвост содержит правила <result><block>. Лимит только от абсурда.
_MAX_DATA_LENGTH = 20000
_RESULT_RE = re.compile(r"<result(?:\s[^>]*)?>\s*(.*?)\s*</result>", re.DOTALL | re.IGNORECASE)
_BLOCK_RE = re.compile(r"<block(?:\s[^>]*)?>\s*(.*?)\s*</block>", re.DOTALL | re.IGNORECASE)
_FENCE_RE = re.compile(r"^\s*```[^\n]*$", re.MULTILINE)
_MIN_TEXT_LEN = 5
_USER_MESSAGE = "Сгенерируй один текст для одного видео."


@dataclass(frozen=True, slots=True)
class _TextBlock:
    """One visual block of a card: its wrapped lines."""

    lines: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Overlay:
    """Text card of one video: the main block and the call to action."""

    blocks: tuple[_TextBlock, ...]


class _TextWriter(Protocol):
    """Port of the text model used by the overlay step."""

    def write(self, system_prompt: str) -> str:
        """Return the raw answer of the model for one overlay."""
        ...


class TextClient:
    """OpenAI-compatible text client of the generator."""

    def __init__(
        self,
        settings: GeneratorSettings,
        *,
        client: httpx.Client | None = None,
        timeout: float = _REQUEST_TIMEOUT,
    ) -> None:
        """Store settings; the HTTP client is created on the first request."""
        self._settings = settings
        self._client = client
        self._timeout = timeout

    def write(self, system_prompt: str) -> str:
        """Ask the model for one card and return its raw answer.

        Args:
            system_prompt: Instruction of the profile (``text_prompt.txt``).

        Returns:
            Raw answer with the ``<result><block>`` markup.

        Raises:
            TextError: The model is unreachable, rejected the request or
                returned an empty answer. The API key is never included.
        """
        headers = {"Content-Type": "application/json"}
        authorization = self._auth_header()
        if authorization is not None:
            headers["Authorization"] = authorization
        url = f"{self._settings.api_url.rstrip('/')}/chat/completions"
        payload = _payload(system_prompt, self._settings.model)
        try:
            if self._client is None:
                with httpx.Client(timeout=self._timeout) as http:
                    response = http.post(url, json=payload, headers=headers)
            else:
                response = self._client.post(
                    url, json=payload, headers=headers, timeout=self._timeout
                )
        except (httpx.TransportError, httpx.TimeoutException) as ex:
            raise TextError("текстовая модель недоступна или не ответила вовремя") from ex
        except httpx.InvalidURL as ex:
            raise TextError("неверный адрес текстовой модели") from ex
        if not response.is_success:
            raise TextError(f"текстовая модель вернула ошибку (код {response.status_code})")
        return _content(response)

    def _auth_header(self) -> str | None:
        """Build the Authorization header without exposing a malformed key."""
        if self._settings.api_key is None:
            return None
        return _bearer(self._settings.api_key.get_secret_value())


def _bearer(key: str) -> str:
    """Return the Bearer header value, rejecting non-printable key characters."""
    return bearer_header(key, TextError, "текстовой модели")


def generate_overlay(
    profile: _Profile,
    writer: _TextWriter,
    *,
    attempts: int = _TEXT_ATTEMPTS,
    pause: float = _TEXT_PAUSE,
    rng: random.Random | None = None,
    sleep: Callable[[float], None] | None = None,
) -> _Overlay:
    """Generate one card, retrying an unavailable model or a bad answer.

    Args:
        profile: _Profile whose ``text_prompt.txt`` drives the model.
        writer: Text model port.
        attempts: Total attempts before the item fails.
        pause: Seconds between attempts.
        rng: Random source for prompt spin options.
        sleep: Pause function; tests replace it to stay fast.

    Returns:
        Parsed overlay of one video.

    Raises:
        TextError: Every attempt failed. Sources on disk are kept, so the
            item can be post-processed again without renting a server.
    """
    randomizer = rng or random.Random()  # noqa: S311 — prompt variety, not secrets
    waiter = sleep or time.sleep
    prompt = _spin(text_prompt(profile), randomizer)
    last = "пустой ответ"
    for attempt in range(1, max(1, attempts) + 1):
        try:
            raw = writer.write(prompt)
        except TextError as ex:
            last = str(ex)
        else:
            overlays = parse_overlays(raw)
            if overlays:
                return overlays[0]
            last = "ответ модели не разобрался"
        logger.bind(profile=profile.name, attempt=attempt, error=last).warning(
            "Текст не получен — повтор"
        )
        if attempt < attempts:
            waiter(pause)
    raise TextError(f"текст для {profile.name} не получен за {attempts} попытки: {last}")


def parse_overlays(raw: str) -> tuple[_Overlay, ...]:
    """Parse the ``<result><block>...</block></result>`` answer.

    An answer whose card text holds a Latin, CJK or replacement character is
    rejected as a whole, so the caller asks the model again instead of drawing
    a mixed-language card.

    Args:
        raw: Raw model answer.

    Returns:
        One overlay per ``<result>``; a plain answer without tags becomes a
        single-block overlay. Empty and too short blocks are skipped.
    """
    text = _clean_answer(raw)
    if not text or _foreign_text(text):
        return ()
    results = _RESULT_RE.findall(text)
    if not results:
        single = _parse_single(text)
        return (single,) if single is not None else ()
    overlays = [overlay for part in results if (overlay := _parse_single(part)) is not None]
    return tuple(overlays)


def save_overlay(path: Path, overlay: _Overlay) -> None:
    """Write the card JSON next to the downloaded source.

    Args:
        path: Target ``text/<name>.json``.
        overlay: Parsed card.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(overlay), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _parse_single(text: str) -> _Overlay | None:
    """Build an overlay from block tags or from plain text."""
    if _BLOCK_RE.search(text):
        blocks = tuple(
            _TextBlock(lines=lines)
            for part in _BLOCK_RE.findall(text)
            if (lines := _lines(part)) and sum(len(line) for line in lines) >= _MIN_TEXT_LEN
        )
        return _Overlay(blocks=blocks) if blocks else None
    lines = _lines(text)
    if lines and sum(len(line) for line in lines) >= _MIN_TEXT_LEN:
        return _Overlay(blocks=(_TextBlock(lines=lines),))
    return None


def _clean_answer(raw: str) -> str:
    """Strip Markdown code fences around the card answer."""
    return _FENCE_RE.sub("", raw).strip()


def _foreign_text(text: str) -> bool:
    """Return whether any extracted card text has a forbidden letter."""
    parts = list(_BLOCK_RE.findall(text))
    parts.extend(_BLOCK_RE.sub(" ", part) for part in _RESULT_RE.findall(text))
    if not parts:
        parts = [text]
    return any(_foreign_lines(_lines(part)) for part in parts)


def _foreign_lines(lines: tuple[str, ...]) -> bool:
    """Return whether a line holds a Latin, CJK or replacement character."""
    return any(_foreign_char(char) for line in lines for char in line)


def _foreign_char(char: str) -> bool:
    """Return whether one character is outside the Cyrillic alphabet.

    Digits, spaces, punctuation, symbols and emoji are allowed; any letter of
    another script and U+FFFD are not.
    """
    if char == "\ufffd":
        return True
    return unicodedata.category(char).startswith("L") and not "\u0400" <= char <= "\u04ff"


def _lines(text: str) -> tuple[str, ...]:
    """Non-empty stripped lines of one block."""
    return tuple(line.strip() for line in text.strip().splitlines() if line.strip())


def _spin(text: str, rng: random.Random) -> str:
    """Expand ``{a|b|c}`` prompt options."""
    return re.sub(
        r"\{([^{}]*\|[^{}]*)\}",
        lambda match: rng.choice(match.group(1).split("|")),
        text,
    )


def _payload(system_prompt: str, model: str) -> dict[str, Any]:
    """Build the OpenAI-compatible chat-completions payload."""
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt[:_MAX_DATA_LENGTH]},
            {"role": "user", "content": _USER_MESSAGE},
        ],
        "temperature": 0.9,
        "stream": False,
    }


def _content(response: httpx.Response) -> str:
    """Extract the non-empty answer text from the chat body."""
    try:
        payload: Any = response.json()
    except ValueError as ex:
        raise TextError("текстовая модель вернула не JSON") from ex
    content = _message_content(payload)
    if not content.strip():
        raise TextError("текстовая модель вернула пустой ответ")
    return content


def _message_content(payload: Any) -> str:
    """Return ``choices[0].message.content`` of the chat body."""
    if not isinstance(payload, Mapping):
        raise TextError("текстовая модель вернула не объект")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise TextError("текстовая модель не вернула choices")
    first = choices[0]
    message = first.get("message") if isinstance(first, Mapping) else None
    content = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(content, str):
        raise TextError("текстовая модель не вернула текст")
    return content
