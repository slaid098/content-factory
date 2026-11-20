"""Tests of the partner delivery page served by the API."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from content_factory.api.app import create_app

_UI_DIR = Path(__file__).resolve().parents[1] / "src" / "content_factory" / "api" / "ui"
_FORBIDDEN = ("Bearer ", "CONTENT_FACTORY_DELIVERY_TOKEN", "Authorization")
_EXTERNAL_URL = re.compile(r"https?://", re.IGNORECASE)
_UI_FILES = sorted(path.name for path in _UI_DIR.iterdir() if path.is_file())
_STATIC_FILES = [name for name in _UI_FILES if name != "index.html"]


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    """Build the app against an empty data directory."""
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    return TestClient(create_app())


def test_root_returns_partner_page(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    html = response.text
    assert 'lang="ru"' in html
    assert 'name="viewport"' in html
    assert 'id="topics"' in html
    assert 'id="archives"' in html
    assert 'id="delivery-form"' in html


def test_root_page_does_not_require_token(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DELIVERY_TOKEN", "secret-delivery-token")

    response = client.get("/")

    assert response.status_code == 200
    assert "secret-delivery-token" not in response.text


@pytest.mark.parametrize("name", _STATIC_FILES)
def test_static_assets_are_served_with_correct_content_type(
    client: TestClient,
    name: str,
) -> None:
    response = client.get(f"/ui/{name}")

    assert response.status_code == 200
    content_type = response.headers["content-type"].split(";")[0]
    if name.endswith(".css"):
        assert content_type == "text/css"
    else:
        assert content_type in {"text/javascript", "application/javascript"}


def test_page_references_only_served_assets(client: TestClient) -> None:
    html = client.get("/").text

    assert 'href="/ui/app.css"' in html
    assert 'src="/ui/app.js"' in html
    assert client.get("/ui/app.css").status_code == 200
    assert client.get("/ui/app.js").status_code == 200


def test_unknown_ui_asset_returns_404(client: TestClient) -> None:
    assert client.get("/ui/missing.css").status_code == 404


@pytest.mark.parametrize("name", _UI_FILES)
def test_ui_files_have_no_secret_and_no_external_urls(name: str) -> None:
    text = (_UI_DIR / name).read_text(encoding="utf-8")

    for needle in _FORBIDDEN:
        assert needle not in text
    assert _EXTERNAL_URL.search(text) is None
    assert "innerHTML" not in text


def test_page_does_not_break_api_and_docs(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    assert "/api/v1/stock" in schema.json()["paths"]

    stock = client.get("/api/v1/stock")
    assert stock.status_code == 200
    assert stock.headers["content-type"].startswith("application/json")


def test_page_uses_same_origin_api_and_long_delivery_timeout() -> None:
    app_script = (_UI_DIR / "app.js").read_text(encoding="utf-8")
    api_script = (_UI_DIR / "api.js").read_text(encoding="utf-8")

    assert '"/api/v1/stock"' in app_script
    assert "crypto.randomUUID()" in app_script
    assert "DELIVERY_TIMEOUT_MS = 300000" in api_script
