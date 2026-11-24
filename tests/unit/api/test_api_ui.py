"""Tests of the partner delivery page served by the API."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from content_factory.api.app import create_app

_UI_DIR = Path(__file__).resolve().parents[3] / "src" / "content_factory" / "api" / "ui"
_FORBIDDEN = ("Bearer ", "CONTENT_FACTORY_DELIVERY_TOKEN", "Authorization")
_EXTERNAL_URL = re.compile(r"https?://", re.IGNORECASE)
_UI_FILES = sorted(path.name for path in _UI_DIR.iterdir() if path.is_file())
_STATIC_FILES = [name for name in _UI_FILES if name != "index.html"]
_JS_FILES = [name for name in _UI_FILES if name.endswith(".js")]
_IMPORT = re.compile(r'from "\./([^"]+)"')


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    """Build the app against an empty data directory."""
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    return TestClient(create_app())


def _ui_text(name: str) -> str:
    return (_UI_DIR / name).read_text(encoding="utf-8")


def _all_scripts() -> str:
    return "\n".join(_ui_text(name) for name in _JS_FILES)


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


def test_root_page_has_four_tabs_and_panels(client: TestClient) -> None:
    html = client.get("/").text

    assert 'role="tablist"' in html
    for name in ("delivery", "queries", "settings", "profiles"):
        assert f'id="tab-{name}"' in html
        assert f'id="panel-{name}"' in html
        assert f'aria-controls="panel-{name}"' in html
        assert f'aria-labelledby="tab-{name}"' in html


def test_delivery_panel_is_open_by_default(client: TestClient) -> None:
    html = client.get("/").text

    assert re.search(r'id="panel-delivery"[^>]*>', html)
    assert not re.search(r'id="panel-delivery"[^>]*hidden', html)
    assert re.search(r'id="panel-queries"[^>]*hidden', html)
    assert re.search(r'id="panel-settings"[^>]*hidden', html)
    assert re.search(r'id="panel-profiles"[^>]*hidden', html)


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
    app_script = _ui_text("app.js")

    assert 'href="/ui/app.css"' in html
    assert 'src="/ui/app.js"' in html
    assert client.get("/ui/app.css").status_code == 200
    assert client.get("/ui/app.js").status_code == 200

    imports = set(_IMPORT.findall(app_script))
    assert {
        "archives.js",
        "jobs.js",
        "profiles.js",
        "queries.js",
        "settings.js",
        "tabs.js",
    } <= imports
    for name in imports:
        assert client.get(f"/ui/{name}").status_code == 200


def test_unknown_ui_asset_returns_404(client: TestClient) -> None:
    assert client.get("/ui/missing.css").status_code == 404


@pytest.mark.parametrize("name", _UI_FILES)
def test_ui_files_have_no_secret_and_no_external_urls(name: str) -> None:
    text = _ui_text(name)

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


def test_delivery_uses_async_link_and_polls_every_three_seconds() -> None:
    scripts = _all_scripts()

    assert "/api/v1/content/deliveries/link" in scripts
    assert "/api/v1/content/deliveries/" in scripts
    assert "POLL_MS = 3000" in scripts
    assert "POLL_LIMIT_MS = 30 * 60 * 1000" in scripts
    for status in ("building", "ready_local", "empty", "failed"):
        assert f'"{status}"' in scripts
    assert "response.status === 409" in scripts


def test_delivery_limit_comes_from_settings_with_fallback() -> None:
    app_script = _ui_text("app.js")

    assert "MAX_COUNT_FALLBACK = 100" in app_script
    assert "delivery" in app_script
    assert "max_count" in app_script
    assert "MAX_COUNT = 10" not in app_script
    assert not re.search(r"Math\.min\(10\b", app_script)


def test_archive_downloads_are_plain_links() -> None:
    archives = _ui_text("archives.js")

    assert "fetch(" not in archives
    assert "readBlob" not in archives
    assert "saveBlob" not in archives
    assert "Blob" not in archives
    assert "item.url" in archives
    assert "/api/v1/content/archives/" in archives
    assert "is-expired" in archives


def test_stock_panel_shows_titles_and_disk_usage() -> None:
    html = _ui_text("index.html")
    app_script = _ui_text("app.js")
    css = _ui_text("app.css")

    assert 'id="archives-summary"' in html
    assert "stock-meta" in app_script
    assert ".stock-meta" in css
    for field in ("titled", "untitled", "video_bytes"):
        assert field in app_script
    assert "formatBytes(row.videoBytes)" in app_script
    assert "с названием" in app_script
    assert "без ·" in app_script
    assert "ждём обработку названий" in app_script


def test_topics_count_all_stock_while_availability_uses_titled() -> None:
    app_script = _ui_text("app.js")

    render_topics = app_script.split("function renderTopics()", 1)[1].split(
        "function buildTopic", 1
    )[0]
    assert "stockCount()" in render_topics
    assert "stockTotal()" not in render_topics
    assert "они входят в «Все темы»" in render_topics

    stock_count = app_script.split("function stockCount()", 1)[1].split("function stockTotal", 1)[0]
    assert "row.stock" in stock_count

    stock_total = app_script.split("function stockTotal()", 1)[1].split(
        "function updateAvailable", 1
    )[0]
    assert "row.titled" in stock_total

    update_available = app_script.split("function updateAvailable()", 1)[1].split(
        "function setBusy", 1
    )[0]
    assert "stockTotal()" in update_available
    assert "stockCount()" not in update_available


def test_archives_header_counts_only_local_archives() -> None:
    archives = _ui_text("archives.js")
    app_script = _ui_text("app.js")

    assert 'summary: byId("archives-summary")' in app_script
    assert 'item.storage === "local"' in archives
    assert "renderSummary" in archives
    assert "formatBytes(bytes)" in archives
    assert "локальные архивы; удалённые в Filebin диск не занимают" in archives


def test_archives_panel_has_no_management_buttons() -> None:
    html = _ui_text("index.html")
    panel = html.split('aria-labelledby="archives-title"', 1)[1].split("</section>", 1)[0]

    assert "<button" not in panel


def test_queries_tab_uses_list_search_and_append() -> None:
    queries = _ui_text("queries.js")

    assert '"/api/v1/content/queries"' in queries
    assert "in_file" in queries
    assert 'method: "POST"' in queries
    assert "Запросов пока нет." in queries


def test_settings_tab_previews_then_applies() -> None:
    settings = _ui_text("settings.js")

    assert '"/api/v1/content/settings"' in settings
    assert '"/api/v1/content/settings/preview"' in settings
    assert '"PUT"' in settings
    assert "Изменений нет." in settings
    assert "elements.apply.disabled = true" in settings
    assert "Бэкап:" in settings


def test_settings_preview_is_invalidated_by_form_changes() -> None:
    settings = _ui_text("settings.js")

    assert "previewPayload" in settings
    assert "serializedForm()" in settings
    assert "body: sent" in settings
    assert "current === null || current !== sent" in settings
    assert "current !== previewPayload" in settings


def test_page_keeps_random_uuid_fallback() -> None:
    app_script = _ui_text("app.js")

    assert "crypto.randomUUID()" in app_script
    assert "crypto.getRandomValues" in app_script


def test_page_uses_same_origin_api_and_short_read_timeout() -> None:
    app_script = _ui_text("app.js")
    api_script = _ui_text("api.js")

    assert '"/api/v1/stock"' in app_script
    assert "READ_TIMEOUT_MS = 15000" in api_script
    assert "DELIVERY_TIMEOUT_MS = 300000" in api_script


def test_profiles_panel_has_list_message_refresh_and_empty_state(
    client: TestClient,
) -> None:
    html = client.get("/").text
    panel = html.split('id="panel-profiles"', 1)[1].split("</main>", 1)[0]

    for element in ("profiles-refresh", "profiles-list", "profiles-message", "profiles-empty"):
        assert f'id="{element}"' in panel
    assert "Профилей пока нет." in panel


def test_profiles_view_loads_on_open_and_refreshes_every_minute() -> None:
    app_script = _ui_text("app.js")
    profiles = _ui_text("profiles.js")

    assert 'from "./profiles.js"' in app_script
    assert "createProfilesView" in app_script
    assert 'byId("tab-profiles")' in app_script
    assert 'byId("panel-profiles")' in app_script
    assert "REFRESH_MS = 60000" in app_script
    assert 'state.activePanel === "panel-profiles"' in app_script
    assert "void profiles.load()" in app_script
    assert '"/api/v1/content/profiles"' in profiles


def test_profiles_view_patches_settings_and_rolls_back() -> None:
    profiles = _ui_text("profiles.js")

    assert 'method: "PATCH"' in profiles
    assert "refill_enabled" in profiles
    assert "refill_at" in profiles
    assert '"target"' in profiles
    assert "JSON.stringify(payload)" in profiles
    assert "Профиль не найден" in profiles
    assert "ERROR_LIMIT = 200" in profiles
    assert "fillInputs()" in profiles


def test_profiles_patch_is_serialized_by_profile_id() -> None:
    profiles = _ui_text("profiles.js")

    assert "const pending = new Set()" in profiles
    assert "pending.has(id)" in profiles
    assert "pending.size > 0" in profiles
    assert "stateVersion" in profiles
    assert "cards.get(id)" in profiles


def test_profiles_cards_are_lazy_safe_and_have_no_generation_button(
    client: TestClient,
) -> None:
    html = client.get("/").text
    profiles = _ui_text("profiles.js")
    scripts = _all_scripts()

    assert 'id="tab-profiles"' in html
    assert 'id="panel-profiles"' in html
    assert 'aria-controls="panel-profiles"' in html
    assert "innerHTML" not in profiles
    assert _EXTERNAL_URL.search(profiles) is None
    assert 'setAttribute("loading", "lazy")' in profiles
    assert "Превью появится после первого ролика" in profiles
    for forbidden in ("Сгенерировать", "Запустить"):
        assert forbidden not in html + scripts


def test_profiles_styles_are_a_wrapping_card_grid() -> None:
    css = _ui_text("app.css")

    for selector in (".profile-list", ".profile-card", ".profile-preview", ".profile-fields"):
        assert selector in css
    assert "object-fit: cover" in css
    assert "grid-template-columns: repeat(auto-fill, minmax(" in css
