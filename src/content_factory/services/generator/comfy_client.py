"""HTTP transport of one ComfyUI server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from content_factory.exceptions.generator import ComfyError
from content_factory.schemas.generator.server import ServerConnection

_REQUEST_TIMEOUT = 120.0
_HTTP_ERROR_LIMIT = 500


@dataclass(frozen=True, slots=True)
class _OutputFile:
    """One output file referenced by the ComfyUI history."""

    filename: str
    subfolder: str = ""
    folder_type: str = "output"


class _ComfyTransport(Protocol):
    """HTTP operations of ComfyUI used by the generation queue."""

    def submit(self, workflow: dict[str, Any]) -> str:
        """Queue a workflow and return its prompt id."""
        ...

    def history(self, prompt_id: str) -> dict[str, Any] | None:
        """Return the finished history item or ``None`` while running."""
        ...

    def queue(self) -> dict[str, Any]:
        """Return the current ``/queue`` payload."""
        ...

    def history_all(self) -> dict[str, Any]:
        """Return the whole ``/history`` payload."""
        ...

    def system_stats(self) -> dict[str, Any]:
        """Return the server health payload of ``/system_stats``."""
        ...

    def download(self, output: _OutputFile) -> bytes:
        """Download one produced file."""
        ...

    def close(self) -> None:
        """Release the HTTP resources of the client."""
        ...


class ComfyClient:
    """Thin REST client of one ComfyUI server."""

    def __init__(
        self,
        connection: ServerConnection,
        *,
        client: httpx.Client | None = None,
        timeout: float = _REQUEST_TIMEOUT,
    ) -> None:
        """Store the server address; the HTTP client is lazy."""
        self._connection = connection
        self._client = client
        self._owned = client is None
        self._timeout = timeout

    def submit(self, workflow: dict[str, Any]) -> str:
        """Queue a workflow; HTTP 400 carries the rejected graph body.

        Raises:
            ComfyError: ComfyUI rejected the graph or returned no prompt id.
        """
        response = self._http().post(f"{self._connection.url()}/prompt", json={"prompt": workflow})
        if response.status_code == 400:
            raise ComfyError(f"ComfyUI отверг workflow: {response.text[:_HTTP_ERROR_LIMIT]}")
        _raise_status(response, "постановка workflow")
        prompt_id = _json_body(response, "постановка workflow").get("prompt_id")
        if not prompt_id:
            raise ComfyError(f"ComfyUI не вернул prompt_id: {response.text[:_HTTP_ERROR_LIMIT]}")
        return str(prompt_id)

    def history(self, prompt_id: str) -> dict[str, Any] | None:
        """Return the finished history item, or ``None`` while generating."""
        response = self._http().get(f"{self._connection.url()}/history/{prompt_id}")
        _raise_status(response, "история ComfyUI")
        item = _json_body(response, "история ComfyUI").get(prompt_id)
        return item if isinstance(item, dict) else None

    def queue(self) -> dict[str, Any]:
        """Return the current ``/queue`` payload of the server."""
        response = self._http().get(f"{self._connection.url()}/queue")
        _raise_status(response, "очередь ComfyUI")
        return _json_body(response, "очередь ComfyUI")

    def history_all(self) -> dict[str, Any]:
        """Return the whole ``/history`` payload of the server."""
        response = self._http().get(f"{self._connection.url()}/history")
        _raise_status(response, "история ComfyUI")
        return _json_body(response, "история ComfyUI")

    def system_stats(self) -> dict[str, Any]:
        """Return the health payload of the server without its raw body.

        Returns:
            JSON object of ``/system_stats``.

        Raises:
            ComfyError: The request failed, returned an HTTP error or a non-JSON body.
        """
        try:
            response = self._http().get(f"{self._connection.url()}/system_stats")
        except httpx.HTTPError as ex:
            raise ComfyError(
                f"ComfyUI не ответил на этапе «проверка здоровья»: {type(ex).__name__}"
            ) from ex
        if not response.is_success:
            raise ComfyError(
                f"ComfyUI: ошибка HTTP {response.status_code} на этапе «проверка здоровья»"
            )
        return _json_body(response, "проверка здоровья")

    def download(self, output: _OutputFile) -> bytes:
        """Download one produced file from the server."""
        response = self._http().get(
            f"{self._connection.url()}/view",
            params={
                "filename": output.filename,
                "subfolder": output.subfolder,
                "type": output.folder_type,
            },
        )
        _raise_status(response, "скачивание результата")
        return response.content

    def close(self) -> None:
        """Close the HTTP client when this instance owns it."""
        if self._owned and self._client is not None and not self._client.is_closed:
            self._client.close()
        if self._owned:
            self._client = None

    def _http(self) -> httpx.Client:
        """Return the lazy HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(timeout=httpx.Timeout(self._timeout, connect=60.0))
        return self._client


def _raise_status(response: httpx.Response, stage: str) -> None:
    """Convert an unsuccessful HTTP response into :class:`ComfyError`."""
    if response.is_success:
        return
    raise ComfyError(
        f"ComfyUI: ошибка HTTP {response.status_code} на этапе «{stage}»: "
        f"{response.text[:_HTTP_ERROR_LIMIT]}"
    )


def _json_body(response: httpx.Response, stage: str) -> dict[str, Any]:
    """Parse a ComfyUI JSON object or raise :class:`ComfyError`."""
    try:
        payload: Any = response.json()
    except ValueError as ex:
        raise ComfyError(f"ComfyUI вернул не JSON на этапе «{stage}»") from ex
    if not isinstance(payload, dict):
        raise ComfyError(f"ComfyUI вернул не объект на этапе «{stage}»")
    return payload


__all__ = ["ComfyClient", "_ComfyTransport", "_OutputFile"]
