"""Действия страницы в открытой сессии BitBrowser MCP."""

from __future__ import annotations

from content_factory.services.supplier.mcp_transport import McpTransport


def _as_text(value: object) -> str:
    return value if isinstance(value, str) else ""


class McpSession:
    """Одна открытая сессия профиля BitBrowser."""

    def __init__(self, transport: McpTransport, profile_id: str) -> None:
        """Создать сессию поверх готового транспорта.

        Args:
            transport: Транспорт MCP-сервера.
            profile_id: Идентификатор открытого профиля.
        """
        self._transport = transport
        self._profile_id = profile_id

    def _path(self, action: str) -> str:
        return self._transport.session_path(self._profile_id, action)

    def navigate(
        self,
        url: str,
        wait_until: str = "domcontentloaded",
        timeout_ms: int | None = None,
    ) -> None:
        """Перейти в сессии по ссылке."""
        payload: dict[str, object] = {"url": url, "wait_until": wait_until}
        if timeout_ms is not None:
            payload["timeout_ms"] = timeout_ms
        self._transport.request("POST", self._path("navigate"), payload)

    def find_by_text_match(self, text: str) -> dict[str, object] | None:
        """Вернуть полный ответ поиска по тексту или None, если текста нет."""
        response = self._transport.request(
            "POST",
            self._path("find_by_text"),
            {"text": text, "exact": False},
        )
        if not response.get("found"):
            return None
        return response

    def find_by_text(self, text: str) -> str | None:
        """Вернуть ref видимого текста или None, если текст не найден."""
        match = self.find_by_text_match(text)
        if match is None:
            return None
        ref = match.get("ref")
        return ref if isinstance(ref, str) and ref else None

    def click(
        self,
        ref: str | None = None,
        selector: str | None = None,
    ) -> None:
        """Кликнуть по элементу через ref или CSS-селектор."""
        if ref is None and selector is None:
            raise ValueError("click requires ref or selector")
        payload: dict[str, object] = {}
        if ref is not None:
            payload["ref"] = ref
        if selector is not None:
            payload["selector"] = selector
        self._transport.request("POST", self._path("click"), payload)

    def get_dom(
        self,
        *,
        max_depth: int = 15,
        max_children: int = 100,
        max_output_chars: int = 500_000,
        skip_hidden: bool = True,
        text_max_length: int = 200,
    ) -> str:
        """Вернуть упрощённое DOM-дерево текущей страницы."""
        response = self._transport.request(
            "POST",
            self._path("dom"),
            {
                "max_depth": max_depth,
                "max_children": max_children,
                "max_output_chars": max_output_chars,
                "skip_hidden": skip_hidden,
                "text_max_length": text_max_length,
            },
        )
        return _as_text(response.get("dom"))

    def get_attribute(
        self,
        attribute: str,
        ref: str | None = None,
        selector: str | None = None,
    ) -> str | None:
        """Вернуть атрибут элемента, заданного через ref или CSS-селектор."""
        if ref is None and selector is None:
            raise ValueError("get_attribute requires ref or selector")
        payload: dict[str, object] = {"attribute": attribute}
        if ref is not None:
            payload["ref"] = ref
        if selector is not None:
            payload["selector"] = selector
        response = self._transport.request("POST", self._path("attribute"), payload)
        value = response.get("value")
        return value if isinstance(value, str) else None

    def wait_for_selector(
        self,
        selector: str,
        timeout_ms: int | None = None,
    ) -> None:
        """Дождаться появления подходящего элемента на странице."""
        payload: dict[str, object] = {"selector": selector}
        if timeout_ms is not None:
            payload["timeout_ms"] = timeout_ms
        self._transport.request("POST", self._path("wait_for_selector"), payload)

    def text(self, selector: str) -> str:
        """Вернуть текст первого подходящего элемента."""
        response = self._transport.request(
            "POST",
            self._path("text"),
            {"selector": selector},
        )
        return _as_text(response.get("text"))

    def attribute(self, selector: str, attribute: str) -> str | None:
        """Вернуть атрибут первого подходящего элемента."""
        response = self._transport.request(
            "POST",
            self._path("attribute"),
            {"selector": selector, "attribute": attribute},
        )
        value = response.get("value")
        return value if isinstance(value, str) else None

    def scroll(self, direction: str = "down", amount: int = 700) -> None:
        """Прокрутить сессию на заданное число пикселей."""
        self._transport.request(
            "POST",
            self._path("scroll"),
            {"direction": direction, "amount": amount},
        )

    def human_idle(self, duration_ms: int) -> None:
        """Выдержать паузу, похожую на человеческую."""
        self._transport.request(
            "POST",
            self._path("human_idle"),
            {"duration_ms": duration_ms},
        )

    def back(self) -> None:
        """Вернуться назад по истории браузера."""
        self._transport.request(
            "POST",
            self._path("back"),
            {"wait_until": "domcontentloaded", "timeout_ms": 30000},
        )

    def url(self) -> str:
        """Вернуть текущий URL страницы."""
        response = self._transport.request("GET", self._path("url"))
        return _as_text(response.get("url"))

    def title(self) -> str:
        """Вернуть заголовок текущей страницы."""
        response = self._transport.request("GET", self._path("title"))
        return _as_text(response.get("title"))

    def status(self) -> dict[str, object]:
        """Вернуть payload статуса сессии."""
        return self._transport.request("GET", self._path("status"))
