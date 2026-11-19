from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from content_factory.exceptions.errors import ConfigError
from content_factory.services.supplier.proxy import fetch_bitbrowser_proxy, resolve_proxy
from supplier_helpers import make_config


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


def _profile(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "id": "profile-1",
        "proxyType": "socks5",
        "host": "1.2.3.4",
        "port": 1080,
        "proxyUserName": "us er",
        "proxyPassword": "p@ss",
    }
    values.update(overrides)
    return values


def test_fetch_bitbrowser_proxy_builds_encoded_url(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _patch_urlopen(
        monkeypatch,
        {"data": {"list": [_profile()]}},
    )

    result = fetch_bitbrowser_proxy("http://127.0.0.1:54529", "profile-1")

    assert result == "socks5://us%20er:p%40ss@1.2.3.4:1080"
    assert requests[0].full_url == "http://127.0.0.1:54529/browser/list"
    assert requests[0].method == "POST"
    body = json.loads((requests[0].data or b"{}").decode("utf-8"))
    assert body == {"page": 0, "pageSize": 500}


def test_fetch_bitbrowser_proxy_http(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(
        monkeypatch,
        {
            "data": {
                "list": [
                    _profile(proxyType="http", proxyUserName="", proxyPassword=""),
                ]
            }
        },
    )

    assert fetch_bitbrowser_proxy("http://bb", "profile-1") == "http://1.2.3.4:1080"


def test_fetch_bitbrowser_proxy_profile_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch, {"data": {"list": [_profile(id="other")]}})

    with pytest.raises(ConfigError, match="не найден"):
        fetch_bitbrowser_proxy("http://bb", "profile-1")


def test_fetch_bitbrowser_proxy_without_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(
        monkeypatch,
        {"data": {"list": [_profile(proxyType="noproxy")]}},
    )

    with pytest.raises(ConfigError, match="не настроен прокси"):
        fetch_bitbrowser_proxy("http://bb", "profile-1")


def test_fetch_bitbrowser_proxy_unsupported_type(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(
        monkeypatch,
        {"data": {"list": [_profile(proxyType="socks4")]}},
    )

    with pytest.raises(ConfigError, match="неподдерживаемый тип"):
        fetch_bitbrowser_proxy("http://bb", "profile-1")


def test_fetch_bitbrowser_proxy_without_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(
        monkeypatch,
        {"data": {"list": [_profile(host="")]}},
    )

    with pytest.raises(ConfigError, match="без хоста или порта"):
        fetch_bitbrowser_proxy("http://bb", "profile-1")


def test_fetch_bitbrowser_proxy_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: int = 0) -> object:
        raise urllib.error.HTTPError(request.full_url, 403, "forbidden", None, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(ConfigError, match="HTTP 403"):
        fetch_bitbrowser_proxy("http://bb", "profile-1")


def test_fetch_bitbrowser_proxy_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: urllib.request.Request, timeout: int = 0) -> object:
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(ConfigError, match="не удалось подключиться"):
        fetch_bitbrowser_proxy("http://bb", "profile-1")


def test_fetch_bitbrowser_proxy_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        def read(self) -> bytes:
            return b"{not-json}"

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: Response())

    with pytest.raises(ConfigError, match="некорректный JSON"):
        fetch_bitbrowser_proxy("http://bb", "profile-1")


def test_fetch_bitbrowser_proxy_unexpected_body(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_urlopen(monkeypatch, {"data": {}})

    with pytest.raises(ConfigError, match="список профилей"):
        fetch_bitbrowser_proxy("http://bb", "profile-1")


def test_fetch_bitbrowser_proxy_rejects_non_object_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_urlopen(monkeypatch, [])

    with pytest.raises(ConfigError, match="неожиданный ответ"):
        fetch_bitbrowser_proxy("http://bb", "profile-1")


def test_resolve_proxy_prefers_explicit_value(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("BitBrowser must not be called")

    monkeypatch.setattr(
        "content_factory.services.supplier.proxy.fetch_bitbrowser_proxy",
        fail,
    )
    cfg = make_config(Path(), proxy="http://127.0.0.1:8080", profile_id="profile-1")

    assert resolve_proxy(cfg) == "http://127.0.0.1:8080"


def test_resolve_proxy_requires_profile_or_proxy() -> None:
    with pytest.raises(ConfigError, match="SUPPLIER_PROFILE_ID"):
        resolve_proxy(make_config(Path(), proxy="", profile_id=""))


def test_resolve_proxy_uses_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_fetch(base_url: str, profile_id: str, timeout: int = 20) -> str:
        calls.append((base_url, profile_id))
        return "http://proxy"

    monkeypatch.setattr(
        "content_factory.services.supplier.proxy.fetch_bitbrowser_proxy",
        fake_fetch,
    )
    cfg = make_config(Path(), proxy="", profile_id="profile-1", bitbrowser_url="http://bb")

    assert resolve_proxy(cfg) == "http://proxy"
    assert calls == [("http://bb", "profile-1")]
