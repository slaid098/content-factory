from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from content_factory.api.app import create_app
from content_factory.exceptions import SettingsWriteError
from settings_helpers import (
    DEFAULT_SETTINGS,
    config_path,
    settings_body,
    write_config,
)

_TOKEN = "settings-secret"
_HEADERS = {"Authorization": f"Bearer {_TOKEN}"}
_BACKUP_RE = re.compile(r"^config\.yaml\.bak-\d{8}-\d{6}(?:-\d+)?$")


@pytest.fixture
def data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the app at a temporary data directory and configure a token."""
    monkeypatch.delenv("CONTENT_FACTORY_DATA_DIR", raising=False)
    monkeypatch.delenv("CONTENT_FACTORY_DELIVERY_TOKEN", raising=False)
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTENT_FACTORY_DELIVERY_TOKEN", _TOKEN)
    return tmp_path


def _client() -> TestClient:
    return TestClient(create_app())


def _get(client: TestClient, path: str, *, token: str | None = _TOKEN) -> object:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.get(path, headers=headers)


def _post(
    client: TestClient,
    body: dict[str, object],
    *,
    token: str | None = _TOKEN,
) -> object:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.post("/api/v1/content/settings/preview", json=body, headers=headers)


def _put(
    client: TestClient,
    body: dict[str, object],
    *,
    token: str | None = _TOKEN,
) -> object:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.put("/api/v1/content/settings", json=body, headers=headers)


def test_settings_without_authorization_returns_401(data_dir: Path) -> None:
    client = _client()

    assert _get(client, "/api/v1/content/settings", token=None).status_code == 401
    assert _post(client, settings_body(), token=None).status_code == 401
    assert _put(client, settings_body(), token=None).status_code == 401


def test_settings_without_configured_token_returns_503(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CONTENT_FACTORY_DELIVERY_TOKEN")

    response = _get(_client(), "/api/v1/content/settings")

    assert response.status_code == 503
    assert _TOKEN not in response.text


def test_get_returns_safe_slice_and_seeds_template(data_dir: Path) -> None:
    response = _get(_client(), "/api/v1/content/settings")

    assert response.status_code == 200
    assert response.json() == DEFAULT_SETTINGS
    assert config_path(data_dir).is_file()
    assert _TOKEN not in response.text
    assert "DELIVERY_TOKEN" not in response.text
    assert "TELEGRAM" not in response.text


def test_get_keeps_duration_strings_from_file(data_dir: Path) -> None:
    write_config(
        data_dir,
        """
run:
  interval: "90m"
  max_duration: "45m"
languages:
  ru:
    enabled: false
    stock_target: 7
  en:
    enabled: true
    stock_target: 8
""",
    )

    response = _get(_client(), "/api/v1/content/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["run"]["interval"] == "90m"
    assert body["run"]["max_duration"] == "45m"
    assert body["languages"] == [
        {"code": "ru", "enabled": False, "stock_target": 7},
        {"code": "en", "enabled": True, "stock_target": 8},
    ]


def test_get_broken_config_returns_500(data_dir: Path) -> None:
    write_config(data_dir, "run: [\n")

    response = _get(_client(), "/api/v1/content/settings")

    assert response.status_code == 500
    assert "YAML" in response.json()["detail"]


def test_preview_returns_diff_without_writing(data_dir: Path) -> None:
    response = _get(_client(), "/api/v1/content/settings")
    original = config_path(data_dir).read_text(encoding="utf-8")

    response = _post(_client(), settings_body(run={"batch": 25}))

    assert response.status_code == 200
    body = response.json()
    assert body["changed"] is True
    assert body["settings"]["run"]["batch"] == 25
    assert "--- config.yaml\n+++ config.yaml\n" in body["diff"]
    assert "-  batch: 20" in body["diff"]
    assert "+  batch: 25" in body["diff"]
    assert config_path(data_dir).read_text(encoding="utf-8") == original


def test_preview_without_changes_returns_empty_diff(data_dir: Path) -> None:
    response = _get(_client(), "/api/v1/content/settings")
    body = response.json()

    response = _post(_client(), body)

    assert response.status_code == 200
    assert response.json()["changed"] is False
    assert response.json()["diff"] == ""
    assert response.json()["settings"] == body


def test_preview_rejects_unknown_field(data_dir: Path) -> None:
    body = settings_body()
    body["unknown"] = True

    response = _post(_client(), body)

    assert response.status_code == 422
    assert not list(config_path(data_dir).parent.glob("config.yaml.bak-*"))


def test_apply_writes_backup_and_keeps_comments(data_dir: Path) -> None:
    _get(_client(), "/api/v1/content/settings")
    path = config_path(data_dir)
    original = path.read_text(encoding="utf-8")

    response = _put(
        _client(),
        settings_body(run={"batch": 25}, delivery={"max_count": 120}),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["changed"] is True
    assert _BACKUP_RE.fullmatch(body["backup"])
    assert path.with_name(body["backup"]).read_text(encoding="utf-8") == original
    updated = path.read_text(encoding="utf-8")
    assert "  batch: 25" in updated
    assert "  max_count: 120" in updated
    assert "# Максимальный объём производства за один тик, видео." in updated


def test_apply_without_changes_creates_no_backup(data_dir: Path) -> None:
    response = _get(_client(), "/api/v1/content/settings")
    body = response.json()
    path = config_path(data_dir)
    original = path.read_text(encoding="utf-8")

    response = _put(_client(), body)

    assert response.status_code == 200
    assert response.json()["changed"] is False
    assert response.json()["backup"] is None
    assert response.json()["diff"] == ""
    assert path.read_text(encoding="utf-8") == original
    assert list(path.parent.glob("config.yaml.bak-*")) == []


def test_apply_adds_missing_delivery(data_dir: Path) -> None:
    write_config(
        data_dir,
        """
run:
  batch: 20
  interval: "6h"
  max_duration: "45m"
  pause_between_languages: "5m"
search:
  max_queries: 30
  pause: "45s"
filter:
  max: 60
  pause: "20s"
download:
  workers: 2
  pause: "10s"
languages:
  ru:
    enabled: true
    stock_target: 150
  en:
    enabled: false
    stock_target: 100
""",
    )

    response = _put(_client(), settings_body())

    assert response.status_code == 200
    assert response.json()["changed"] is True
    assert "delivery:" in config_path(data_dir).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "body",
    [
        settings_body(run={"batch": 0}),
        settings_body(run={"batch": 1001}),
        settings_body(run={"batch": True}),
        settings_body(run={"batch": "20"}),
        settings_body(run={"interval": ""}),
        settings_body(run={"interval": "6 часов"}),
        settings_body(run={"max_duration": "0h"}),
        settings_body(search={"max_queries": 0}),
        settings_body(search={"max_queries": 501}),
        settings_body(filter={"max": 0}),
        settings_body(filter={"max": 10001}),
        settings_body(download={"workers": 0}),
        settings_body(download={"workers": 9}),
        settings_body(delivery={"max_count": 0}),
        settings_body(delivery={"max_count": 301}),
        settings_body(
            languages=[
                {"code": "ru", "enabled": True, "stock_target": -1},
                {"code": "en", "enabled": False, "stock_target": 100},
            ]
        ),
        settings_body(
            languages=[
                {"code": "ru", "enabled": 1, "stock_target": 150},
                {"code": "en", "enabled": False, "stock_target": 100},
            ]
        ),
        settings_body(
            languages=[
                {"code": "ru", "enabled": True, "stock_target": 100001},
                {"code": "en", "enabled": False, "stock_target": 100},
            ]
        ),
    ],
)
def test_apply_rejects_invalid_values(data_dir: Path, body: dict[str, object]) -> None:
    _get(_client(), "/api/v1/content/settings")
    path = config_path(data_dir)
    original = path.read_text(encoding="utf-8")

    response = _put(_client(), body)

    assert response.status_code == 422
    assert path.read_text(encoding="utf-8") == original
    assert list(path.parent.glob("config.yaml.bak-*")) == []


@pytest.mark.parametrize(
    "languages",
    [
        [
            {"code": "ru", "enabled": True, "stock_target": 150},
            {"code": "de", "enabled": True, "stock_target": 100},
        ],
        [{"code": "ru", "enabled": True, "stock_target": 150}],
        [
            {"code": "ru", "enabled": True, "stock_target": 150},
            {"code": "ru", "enabled": False, "stock_target": 100},
        ],
    ],
)
def test_apply_rejects_language_changes(
    data_dir: Path,
    languages: list[dict[str, object]],
) -> None:
    _get(_client(), "/api/v1/content/settings")
    path = config_path(data_dir)
    original = path.read_text(encoding="utf-8")

    response = _put(_client(), settings_body(languages=languages))

    assert response.status_code == 422
    assert path.read_text(encoding="utf-8") == original


def test_apply_rejects_max_duration_below_pause(data_dir: Path) -> None:
    path = write_config(
        data_dir,
        """
run:
  max_duration: "3h"
  pause_between_languages: "2h"
languages:
  ru:
    enabled: true
    stock_target: 150
  en:
    enabled: false
    stock_target: 100
""",
    )
    original = path.read_text(encoding="utf-8")

    response = _put(_client(), settings_body())

    assert response.status_code == 422
    assert "pause_between_languages" in response.json()["detail"]
    assert path.read_text(encoding="utf-8") == original


def test_apply_broken_yaml_returns_422(data_dir: Path) -> None:
    path = write_config(data_dir, "run: [\n")

    response = _put(_client(), settings_body())

    assert response.status_code == 422
    assert "YAML" in response.json()["detail"]
    assert path.read_text(encoding="utf-8") == "run: [\n"


def test_apply_unknown_key_in_file_returns_422(data_dir: Path) -> None:
    path = write_config(data_dir, "unknown: 1\n")

    response = _put(_client(), settings_body())

    assert response.status_code == 422
    assert "unknown" in response.json()["detail"]
    assert path.read_text(encoding="utf-8") == "unknown: 1\n"


def test_apply_write_error_returns_500(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _get(_client(), "/api/v1/content/settings")
    path = config_path(data_dir)
    original = path.read_text(encoding="utf-8")

    def fail(*args: object, **kwargs: object) -> str:
        raise SettingsWriteError("boom")

    monkeypatch.setattr(
        "content_factory.services.settings.service.write_with_backup",
        fail,
    )

    response = _put(_client(), settings_body(run={"batch": 25}))

    assert response.status_code == 500
    assert response.json()["detail"] == "settings write failed"
    assert path.read_text(encoding="utf-8") == original
