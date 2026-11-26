"""HTTP tests of the partner delivery page served by the API.

The page source lives in ``frontend/`` and is covered by Vitest/Playwright;
here only the served page and its static assets are checked.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from content_factory.api.app import create_app

_UI_DIR = Path(__file__).resolve().parents[3] / "src" / "content_factory" / "api" / "ui"
_ASSETS = ("app.css", "app.js")


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
    assert 'id="topics"' not in html
    assert 'id="archives"' in html
    assert 'id="delivery-form"' in html
    assert 'id="delivery-source"' in html
    assert 'id="general-title"' in html
    assert 'id="profiles-stock"' in html


def test_root_page_has_four_tabs_and_panels(client: TestClient) -> None:
    html = client.get("/").text

    assert 'role="tablist"' in html
    assert re.findall(r'id="tab-([a-z]+)"', html) == [
        "delivery",
        "queries",
        "profiles",
        "settings",
    ]
    for name in ("delivery", "queries", "profiles", "settings"):
        assert f'id="tab-{name}"' in html
        assert f'id="panel-{name}"' in html
        assert f'aria-controls="panel-{name}"' in html
        assert f'aria-labelledby="tab-{name}"' in html


def test_root_page_has_no_hero_or_footer(client: TestClient) -> None:
    html = client.get("/").text

    assert "<footer" not in html
    assert 'class="lead"' not in html
    assert 'class="eyebrow"' not in html


def test_delivery_panel_is_open_by_default(client: TestClient) -> None:
    html = client.get("/").text

    delivery = re.search(r'<section[^>]*id="panel-delivery"[^>]*>', html)
    assert delivery is not None
    assert "hidden" not in delivery.group(0)
    for name in ("queries", "settings", "profiles"):
        panel = re.search(rf'<section[^>]*id="panel-{name}"[^>]*>', html)
        assert panel is not None
        assert "hidden" in panel.group(0)


def test_root_page_does_not_require_token(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTENT_FACTORY_DELIVERY_TOKEN", "secret-delivery-token")

    response = client.get("/")

    assert response.status_code == 200
    assert "secret-delivery-token" not in response.text


@pytest.mark.parametrize("name", _ASSETS)
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
    for name in _ASSETS:
        assert client.get(f"/ui/{name}").status_code == 200


def test_unknown_ui_asset_returns_404(client: TestClient) -> None:
    assert client.get("/ui/missing.css").status_code == 404


def test_ui_dir_contains_the_page_and_assets() -> None:
    names = {path.name for path in _UI_DIR.iterdir() if path.is_file()}

    assert {"index.html", *_ASSETS} <= names


def test_page_does_not_break_api_and_docs(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    assert "/api/v1/stock" in schema.json()["paths"]

    stock = client.get("/api/v1/stock")
    assert stock.status_code == 200
    assert stock.headers["content-type"].startswith("application/json")
