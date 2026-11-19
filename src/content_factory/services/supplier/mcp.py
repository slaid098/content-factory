"""Синхронный клиент BitBrowser MCP: транспорт, профиль и сессии."""

from __future__ import annotations

from content_factory.services.supplier.mcp_session import McpSession
from content_factory.services.supplier.mcp_transport import McpTransport


class McpClient:
    """Тонкий синхронный клиент REST API BitBrowser MCP.

    Клиент открывает и закрывает профиль, а действия страницы отдаёт
    ``McpSession``. Выполнение JavaScript не входит в контракт поставщика.
    """

    def __init__(self, base_url: str, timeout: int = 30) -> None:
        """Создать клиент одного MCP-сервера.

        Args:
            base_url: Корень MCP-сервера, например ``http://127.0.0.1:8765``.
            timeout: Таймаут одного запроса в секундах.
        """
        self._transport = McpTransport(base_url, timeout)

    @property
    def base_url(self) -> str:
        """Вернуть настроенный корень MCP-сервера."""
        return self._transport.base_url

    def open_profile(self, profile_id: str, headless: bool = False) -> None:
        """Открыть профиль BitBrowser и дождаться его сессии."""
        self._transport.request(
            "POST",
            "/api/v1/profiles/open",
            {"profile_id": profile_id, "headless": headless},
        )

    def close_profile(self, profile_id: str) -> None:
        """Закрыть профиль BitBrowser."""
        self._transport.request(
            "POST",
            self._transport.profile_path(profile_id, "close"),
        )

    def session(self, profile_id: str) -> McpSession:
        """Вернуть страничную сессию уже открытого профиля.

        Args:
            profile_id: Идентификатор профиля BitBrowser.

        Returns:
            Объект с действиями страницы.
        """
        return McpSession(self._transport, profile_id)
