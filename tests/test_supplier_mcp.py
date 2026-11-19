from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from content_factory.exceptions.errors import SupplierError
from content_factory.services.supplier.mcp import McpClient
from content_factory.services.supplier.mcp_session import McpSession


class FakeResponse:
    def __init__(self, payload: object) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _patch_urlopen(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
) -> list[urllib.request.Request]:
    requests: list[urllib.request.Request] = []

    def fake_urlopen(request: urllib.request.Request, timeout: int = 0) -> FakeResponse:
        requests.append(request)
        return FakeResponse(payload)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return requests


def _body(request: urllib.request.Request) -> dict[str, Any]:
    assert request.data is not None
    decoded: object = json.loads(request.data.decode("utf-8"))
    assert isinstance(decoded, dict)
    return decoded


def _assert_request(
    request: urllib.request.Request,
    method: str,
    path: str,
) -> None:
    assert request.full_url == f"http://mcp{path}"
    assert request.method == method


def _session() -> McpSession:
    return McpClient("http://mcp").session("p")


def test_base_url_is_normalized() -> None:
    client = McpClient("http://127.0.0.1:8765/")
    assert client.base_url == "http://127.0.0.1:8765"


def test_open_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {"session_id": "sess"})
    McpClient("http://mcp").open_profile("profile-1", headless=True)
    assert requests[0].full_url == "http://mcp/api/v1/profiles/open"
    assert requests[0].method == "POST"
    assert _body(requests[0]) == {"profile_id": "profile-1", "headless": True}


def test_close_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {})
    McpClient("http://mcp").close_profile("profile-1")
    assert requests[0].full_url == "http://mcp/api/v1/profiles/profile-1/close"
    assert requests[0].method == "POST"


def test_navigate(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {})
    _session().navigate("https://tiktok.com/foryou", timeout_ms=1000)
    _assert_request(requests[0], "POST", "/api/v1/sessions/p/navigate")
    assert _body(requests[0]) == {
        "url": "https://tiktok.com/foryou",
        "wait_until": "domcontentloaded",
        "timeout_ms": 1000,
    }


def test_navigate_without_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {})
    _session().navigate("https://tiktok.com/foryou")
    assert _body(requests[0]) == {
        "url": "https://tiktok.com/foryou",
        "wait_until": "domcontentloaded",
    }


def test_find_by_text_found(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {"found": True, "ref": "find_1"})
    assert _session().find_by_text("author") == "find_1"
    _assert_request(requests[0], "POST", "/api/v1/sessions/p/find_by_text")
    assert _body(requests[0]) == {"text": "author", "exact": False}


def test_find_by_text_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {"found": False})
    assert _session().find_by_text("author") is None
    _assert_request(requests[0], "POST", "/api/v1/sessions/p/find_by_text")
    assert _body(requests[0]) == {"text": "author", "exact": False}


def test_find_by_text_empty_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {"found": True, "ref": ""})
    assert _session().find_by_text("author") is None
    _assert_request(requests[0], "POST", "/api/v1/sessions/p/find_by_text")
    assert _body(requests[0]) == {"text": "author", "exact": False}


def test_find_by_text_match_returns_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"found": True, "ref": "find_1", "tag": "A", "text": "author"}
    requests = _patch_urlopen(monkeypatch, payload)
    match = _session().find_by_text_match("author")
    assert match == payload
    _assert_request(requests[0], "POST", "/api/v1/sessions/p/find_by_text")
    assert _body(requests[0]) == {"text": "author", "exact": False}


def test_find_by_text_match_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {"found": False})
    assert _session().find_by_text_match("author") is None
    _assert_request(requests[0], "POST", "/api/v1/sessions/p/find_by_text")
    assert _body(requests[0]) == {"text": "author", "exact": False}


def test_click_by_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {})
    _session().click(ref="find_1")
    _assert_request(requests[0], "POST", "/api/v1/sessions/p/click")
    assert _body(requests[0]) == {"ref": "find_1"}


def test_click_by_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {})
    _session().click(selector="#button")
    _assert_request(requests[0], "POST", "/api/v1/sessions/p/click")
    assert _body(requests[0]) == {"selector": "#button"}


def test_click_requires_target() -> None:
    with pytest.raises(ValueError, match="ref or selector"):
        _session().click()


def test_text(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {"text": "673"})
    assert _session().text("body") == "673"
    _assert_request(requests[0], "POST", "/api/v1/sessions/p/text")
    assert _body(requests[0]) == {"selector": "body"}


def test_text_missing_value(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {})
    assert _session().text("body") == ""
    _assert_request(requests[0], "POST", "/api/v1/sessions/p/text")
    assert _body(requests[0]) == {"selector": "body"}


def test_attribute(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {"value": "https://tiktok.com/video/1"})
    assert _session().attribute("a", "href") == "https://tiktok.com/video/1"
    _assert_request(requests[0], "POST", "/api/v1/sessions/p/attribute")
    assert _body(requests[0]) == {"selector": "a", "attribute": "href"}


def test_attribute_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {"value": None})
    assert _session().attribute("a", "href") is None
    _assert_request(requests[0], "POST", "/api/v1/sessions/p/attribute")
    assert _body(requests[0]) == {"selector": "a", "attribute": "href"}


def test_scroll(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {})
    _session().scroll("up", 400)
    _assert_request(requests[0], "POST", "/api/v1/sessions/p/scroll")
    assert _body(requests[0]) == {"direction": "up", "amount": 400}


def test_human_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {})
    _session().human_idle(2500)
    _assert_request(requests[0], "POST", "/api/v1/sessions/p/human_idle")
    assert _body(requests[0]) == {"duration_ms": 2500}


def test_back(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {})
    _session().back()
    _assert_request(requests[0], "POST", "/api/v1/sessions/p/back")
    assert _body(requests[0])["wait_until"] == "domcontentloaded"


def test_url_and_title(monkeypatch: pytest.MonkeyPatch) -> None:
    client = McpClient("http://mcp")
    url_requests = _patch_urlopen(monkeypatch, {"url": "https://tiktok.com/@user"})
    assert client.session("p").url() == "https://tiktok.com/@user"
    _assert_request(url_requests[0], "GET", "/api/v1/sessions/p/url")
    title_requests = _patch_urlopen(monkeypatch, {"title": "TikTok"})
    assert client.session("p").title() == "TikTok"
    _assert_request(title_requests[0], "GET", "/api/v1/sessions/p/title")


def test_status(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {"alive": True, "url": "https://tiktok.com"})
    assert _session().status() == {"alive": True, "url": "https://tiktok.com"}
    _assert_request(requests[0], "GET", "/api/v1/sessions/p/status")


def test_http_error_includes_url_and_status(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: int = 0) -> object:
        raise urllib.error.HTTPError(request.full_url, 403, "forbidden", None, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(SupplierError, match=r"HTTP 403") as error:
        _session().text("body")
    assert "http://mcp/api/v1/sessions/p/text" in str(error.value)


def test_connection_error_includes_url(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: int = 0) -> object:
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(SupplierError, match="connection error") as error:
        _session().url()
    assert "http://mcp/api/v1/sessions/p/url" in str(error.value)


def test_malformed_url_is_wrapped() -> None:
    with pytest.raises(SupplierError, match="invalid request"):
        McpClient("").session("p").title()


def test_http_client_error_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: int = 0) -> object:
        raise http.client.HTTPException("broken")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(SupplierError, match="invalid request"):
        _session().url()


def test_invalid_utf8_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        def read(self) -> bytes:
            return b"\xff\xfe"

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: Response())
    with pytest.raises(SupplierError, match="invalid UTF-8"):
        _session().title()


def test_error_messages_strip_userinfo(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: int = 0) -> object:
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(SupplierError) as error:
        McpClient("http://user:secret@mcp:8765").session("p").url()

    message = str(error.value)
    assert "secret" not in message
    assert "user" not in message
    assert "http://mcp:8765/api/v1/sessions/p/url" in message


def test_error_messages_strip_userinfo_without_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: int = 0) -> object:
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(SupplierError) as error:
        McpClient("http://user:secret@mcp").session("p").url()

    message = str(error.value)
    assert "secret" not in message
    assert "http://mcp/api/v1/sessions/p/url" in message


def test_error_messages_hide_malformed_url(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: int = 0) -> object:
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(SupplierError) as error:
        McpClient("http://user:secret@mcp:bad").session("p").url()

    assert str(error.value).endswith("connection error")
    assert "secret" not in str(error.value)


def test_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        def read(self) -> bytes:
            return b"{not-json}"

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: Response())
    with pytest.raises(SupplierError, match="invalid JSON"):
        _session().title()


def test_unexpected_json(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch, ["not", "an", "object"])
    with pytest.raises(SupplierError, match="unexpected JSON"):
        _session().title()


def test_empty_response_is_empty_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        def read(self) -> bytes:
            return b""

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: Response())
    assert _session().status() == {}


def test_no_javascript_method() -> None:
    assert not hasattr(McpClient, "execute_js")


def test_get_dom(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {"dom": "<div>tree</div>"})
    assert _session().get_dom(max_depth=12) == "<div>tree</div>"
    _assert_request(requests[0], "POST", "/api/v1/sessions/p/dom")
    body = _body(requests[0])
    assert body["max_depth"] == 12
    assert body["max_children"] == 100
    assert body["max_output_chars"] == 500_000
    assert body["skip_hidden"] is True
    assert body["text_max_length"] == 200


def test_get_dom_missing_value(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {})
    assert _session().get_dom() == ""
    _assert_request(requests[0], "POST", "/api/v1/sessions/p/dom")


def test_get_attribute_by_ref(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {"value": "https://tiktok.com/video/1"})
    assert _session().get_attribute("href", ref="r1") == "https://tiktok.com/video/1"
    _assert_request(requests[0], "POST", "/api/v1/sessions/p/attribute")
    assert _body(requests[0]) == {"attribute": "href", "ref": "r1"}


def test_get_attribute_by_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {"value": "x"})
    assert _session().get_attribute("href", selector="a") == "x"
    _assert_request(requests[0], "POST", "/api/v1/sessions/p/attribute")
    assert _body(requests[0]) == {"attribute": "href", "selector": "a"}


def test_get_attribute_missing_value(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {"value": None})
    assert _session().get_attribute("href", ref="r1") is None
    _assert_request(requests[0], "POST", "/api/v1/sessions/p/attribute")


def test_get_attribute_requires_target() -> None:
    with pytest.raises(ValueError, match="ref or selector"):
        _session().get_attribute("href")


def test_wait_for_selector(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {"found": True})
    _session().wait_for_selector("a[href]", timeout_ms=5000)
    _assert_request(requests[0], "POST", "/api/v1/sessions/p/wait_for_selector")
    assert _body(requests[0]) == {"selector": "a[href]", "timeout_ms": 5000}


def test_wait_for_selector_without_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(monkeypatch, {"found": True})
    _session().wait_for_selector("a[href]")
    _assert_request(requests[0], "POST", "/api/v1/sessions/p/wait_for_selector")
    assert _body(requests[0]) == {"selector": "a[href]"}
