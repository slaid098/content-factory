"""HTTP client of the Clef decision model used by the safety check."""

from __future__ import annotations

import base64
import math
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

import httpx
from loguru import logger

from content_factory.config.settings import GeneratorSettings
from content_factory.exceptions.generator import SafetyError
from content_factory.services.generator.auth import bearer_header

_CLEF_ATTEMPTS = 2
_CLEF_PAUSE = 1.0
_REQUEST_TIMEOUT = 30.0
# 408, 425 and 429 mean "try later", not a rejected key or payload: one pause
# and one repeat are cheaper than failing the video while the server is busy.
_RETRY_STATUSES = frozenset({408, 425, 429})
_STATE = "Кадр из вертикального видео."
QUESTIONS: dict[str, str] = {
    "back": "человек стоит спиной или повёрнут задом",
    "short_skirt": "юбка выше колена",
    "cleavage": "глубокий вырез",
    "all_skin": "вся одежда телесного, розового или белого цвета",
    "nude": "нагота или обнажённые части тела",
}


class _ClefTransport(Protocol):
    """Clef request used by the preflight and the frame check."""

    def scores(self, png: bytes) -> dict[str, float]:
        """Return the ``noul`` score of every safety question for one frame."""
        ...


class ClefClient:
    """HTTP client of the Clef decision model."""

    def __init__(
        self,
        settings: GeneratorSettings,
        *,
        client: httpx.Client | None = None,
        timeout: float = _REQUEST_TIMEOUT,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Store the endpoint; the HTTP client is created on the first request."""
        self._url = f"{settings.api_url.rstrip('/')}/systemone"
        self._model = settings.clef_model
        self._key = settings.api_key
        self._client = client
        self._timeout = timeout
        self._sleep = sleep

    def scores(self, png: bytes) -> dict[str, float]:
        """Ask Clef about one frame and return every ``noul`` score.

        A network error or a 5xx answer is retried once; an unusable body,
        a missing answer or a score outside ``0..1`` fails at once.

        Args:
            png: Encoded frame image.

        Returns:
            Scores keyed by the safety question names.

        Raises:
            SafetyError: Clef is unreachable, rejected the request or
                returned an answer that cannot be trusted. The API key is
                never included in the message.
        """
        payload = _payload(png, self._model)
        headers = self._headers()
        last = "нет ответа"
        for attempt in range(1, _CLEF_ATTEMPTS + 1):
            try:
                response = self._post(payload, headers)
            except httpx.InvalidURL as ex:
                raise SafetyError("неверный адрес Clef") from ex
            except httpx.RequestError as ex:
                last = "Clef недоступен или не ответил вовремя"
                logger.bind(attempt=attempt, error=str(ex), error_type=type(ex).__name__).warning(
                    "Clef не ответил — повтор"
                )
            else:
                if response.is_success:
                    return _scores(response)
                if response.status_code < 500 and response.status_code not in _RETRY_STATUSES:
                    raise SafetyError(f"Clef вернул ошибку (код {response.status_code})")
                last = f"Clef вернул ошибку (код {response.status_code})"
                logger.bind(attempt=attempt, status=response.status_code).warning(
                    "Clef ответил ошибкой — повтор"
                )
            if attempt < _CLEF_ATTEMPTS:
                self._sleep(_CLEF_PAUSE)
        raise SafetyError(f"Clef не ответил за {_CLEF_ATTEMPTS} попытки: {last}")

    def _headers(self) -> dict[str, str]:
        """Build the JSON and optional Bearer headers without exposing the key."""
        headers = {"Content-Type": "application/json"}
        if self._key is not None:
            headers["Authorization"] = bearer_header(
                self._key.get_secret_value(), SafetyError, "Clef"
            )
        return headers

    def _post(self, payload: dict[str, Any], headers: dict[str, str]) -> httpx.Response:
        """Send one request, reusing an injected client when the tests pass one."""
        if self._client is None:
            with httpx.Client(timeout=self._timeout) as http:
                return http.post(self._url, json=payload, headers=headers)
        return self._client.post(self._url, json=payload, headers=headers, timeout=self._timeout)


def _payload(png: bytes, model: str) -> dict[str, Any]:
    """Build the System One request with one image and the safety questions."""
    return {
        "model": model,
        "state": _STATE,
        "images": [base64.b64encode(png).decode("ascii")],
        "questions": {
            key: {"type": "noul", "instructions": instructions}
            for key, instructions in QUESTIONS.items()
        },
    }


def _scores(response: httpx.Response) -> dict[str, float]:
    """Parse and validate every ``answers[key].noul`` of one response."""
    try:
        payload: Any = response.json()
    except ValueError as ex:
        raise SafetyError("Clef вернул не JSON") from ex
    if not isinstance(payload, Mapping):
        raise SafetyError("Clef вернул не объект")
    answers = payload.get("answers")
    if not isinstance(answers, Mapping):
        raise SafetyError("Clef не вернул answers")
    scores: dict[str, float] = {}
    for key in QUESTIONS:
        answer = answers.get(key)
        value = answer.get("noul") if isinstance(answer, Mapping) else None
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise SafetyError(f"Clef не вернул число для вопроса {key}")
        number = float(value)
        if not math.isfinite(number) or not 0.0 <= number <= 1.0:
            raise SafetyError(f"Clef вернул вне 0..1 для вопроса {key}: {number}")
        scores[key] = number
    return scores


__all__ = ["ClefClient", "QUESTIONS"]
