"""HTTP-транспорт REST API BitBrowser MCP."""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from content_factory.exceptions.errors import SupplierError

_JSON_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}


def _quote(profile_id: str) -> str:
    return urllib.parse.quote(profile_id, safe="")


def _display_url(url: str) -> str:
    """Return *url* without userinfo so credentials never reach logs."""
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return "<invalid url>"
    if parts.username is None and parts.password is None:
        return url
    netloc = parts.hostname or ""
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


class McpTransport:
    """Минимальный JSON-транспорт до одного MCP-сервера."""

    def __init__(self, base_url: str, timeout: int = 30) -> None:
        """Создать транспорт одного MCP-сервера.

        Args:
            base_url: Корень MCP-сервера, например ``http://127.0.0.1:8765``.
            timeout: Таймаут одного запроса в секундах.
        """
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    @property
    def base_url(self) -> str:
        """Вернуть настроенный корень MCP-сервера."""
        return self._base_url

    def session_path(self, profile_id: str, action: str) -> str:
        """Собрать путь действия внутри сессии профиля."""
        return f"/api/v1/sessions/{_quote(profile_id)}/{action}"

    def profile_path(self, profile_id: str, action: str) -> str:
        """Собрать путь действия над профилем."""
        return f"/api/v1/profiles/{_quote(profile_id)}/{action}"

    def request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        """Выполнить запрос и вернуть JSON-объект ответа."""
        url = f"{self._base_url}{path}"
        safe_url = _display_url(url)
        try:
            data = json.dumps(payload).encode("utf-8") if payload is not None else None
            request = urllib.request.Request(
                url,
                data=data,
                method=method,
                headers=_JSON_HEADERS,
            )
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                raw_bytes = response.read()
        except urllib.error.HTTPError as ex:
            raise SupplierError(f"MCP call failed: {method} {safe_url} -> HTTP {ex.code}") from ex
        except (urllib.error.URLError, TimeoutError, OSError) as ex:
            raise SupplierError(f"MCP call failed: {method} {safe_url} -> connection error") from ex
        except (TypeError, ValueError, http.client.HTTPException) as ex:
            raise SupplierError(f"MCP call failed: {method} {safe_url} -> invalid request") from ex

        try:
            raw = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as ex:
            raise SupplierError(f"MCP call failed: {method} {safe_url} -> invalid UTF-8") from ex
        if not raw:
            return {}
        try:
            decoded: object = json.loads(raw)
        except json.JSONDecodeError as ex:
            raise SupplierError(f"MCP call failed: {method} {safe_url} -> invalid JSON") from ex
        if not isinstance(decoded, dict):
            raise SupplierError(f"MCP call failed: {method} {safe_url} -> unexpected JSON")
        return decoded
