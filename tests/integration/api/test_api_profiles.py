"""Profile API: listing, refill settings and preview."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from tests.unit.common.profile_helpers import (
    add_profile,
    add_profile_video,
    set_profile_refill,
    write_run_report,
)

from content_factory.api.app import create_app
from content_factory.constants import CONFIG_DIR, DB_DIR, PREVIEW_DIR
from content_factory.services.profiles import preview

_TOKEN = "delivery-secret"
_PREFIX = "/api/v1/content/profiles"


@pytest.fixture
def data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the app at a temporary data directory and configure a token."""
    for name in (
        "CONTENT_FACTORY_DATA_DIR",
        "CONTENT_FACTORY_DELIVERY_TOKEN",
        "GENERATOR_OUTPUT_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTENT_FACTORY_DELIVERY_TOKEN", _TOKEN)
    monkeypatch.setenv("GENERATOR_OUTPUT_DIR", str(tmp_path / "generated"))
    return tmp_path


def _client() -> TestClient:
    return TestClient(create_app())


def _headers(token: str | None = _TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token is not None else {}


def _get(
    client: TestClient,
    path: str,
    *,
    token: str | None = _TOKEN,
) -> httpx.Response:
    return client.get(path, headers=_headers(token))


def _patch(
    client: TestClient,
    path: str,
    payload: dict[str, object],
    *,
    token: str | None = _TOKEN,
) -> httpx.Response:
    return client.patch(path, json=payload, headers=_headers(token))


def _fake_extract(calls: list[Path]) -> Any:
    def extract(source: Path, target: Path) -> None:
        calls.append(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\xff\xd8frame")

    return extract


def _failed_item(error: str) -> dict[str, Any]:
    return {
        "file": "001.mp4",
        "profile": "dating/profile_1",
        "look_id": "look-1",
        "seed": 1,
        "frames": 10,
        "status": "failed",
        "error": error,
    }


def test_list_returns_profiles_with_stock_overrides_and_last_run(data_dir: Path) -> None:
    add_profile(data_dir, "dating", "profile_1", audio=False)
    add_profile(data_dir, "auto", "profile_2", language="en")
    add_profile_video(data_dir, "dating", "profile_1", filename="dating_profile_1_001.mp4")
    add_profile_video(data_dir, "dating", "profile_1", filename="dating_profile_1_002.mp4")
    add_profile_video(data_dir, "auto", "profile_2", language="en")
    set_profile_refill(data_dir, "dating/profile_1", enabled=True, target=5, refill_at=1)
    write_run_report(
        data_dir / "generated",
        generated=2,
        failed=1,
        items=[_failed_item("битый кадр")],
    )

    response = _get(_client(), _PREFIX)

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["profiles"]] == [
        "auto/profile_2",
        "dating/profile_1",
    ]
    first, second = body["profiles"]
    assert first == {
        "id": "auto/profile_2",
        "category": "auto",
        "name": "profile_2",
        "language": "en",
        "niche": "auto",
        "width": 544,
        "height": 960,
        "audio": True,
        "stock": 1,
        "target": 20,
        "refill_at": 2,
        "refill_enabled": False,
        "target_override": None,
        "refill_at_override": None,
        "preview_url": "/api/v1/content/profiles/auto/profile_2/preview",
        "last_run": None,
    }
    assert second["stock"] == 2
    assert second["audio"] is False
    assert second["target"] == 5
    assert second["refill_at"] == 1
    assert second["refill_enabled"] is True
    assert second["target_override"] == 5
    assert second["refill_at_override"] == 1
    assert second["last_run"] == {
        "status": "done",
        "finished_at": "2026-05-01T12:30:00Z",
        "generated": 2,
        "failed": 1,
        "error": "битый кадр",
    }
    assert _TOKEN not in response.text


def test_list_without_profiles_returns_empty_array(data_dir: Path) -> None:
    response = _get(_client(), _PREFIX)

    assert response.status_code == 200
    assert response.json() == {"profiles": []}


def test_list_does_not_change_stored_settings(data_dir: Path) -> None:
    add_profile(data_dir, "dating", "profile_1")
    client = _client()

    assert _get(client, _PREFIX).status_code == 200

    connection = sqlite3.connect(data_dir / DB_DIR / "db.sqlite3")
    try:
        rows = connection.execute("SELECT * FROM profile_settings").fetchall()
    finally:
        connection.close()
    assert rows == []


def test_list_survives_broken_stock_database(data_dir: Path) -> None:
    add_profile(data_dir, "dating", "profile_1")
    (data_dir / DB_DIR).write_text("not a directory", encoding="utf-8")

    response = _get(_client(), _PREFIX)

    assert response.status_code == 200
    assert response.json()["profiles"][0]["stock"] == 0
    assert response.json()["profiles"][0]["target"] == 20


def test_list_returns_500_on_broken_runtime_config(data_dir: Path) -> None:
    config = data_dir / CONFIG_DIR / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("generator: [\n", encoding="utf-8")

    response = _get(_client(), _PREFIX)

    assert response.status_code == 500
    assert response.json() == {"detail": "profiles unavailable"}


def test_get_returns_one_profile(data_dir: Path) -> None:
    add_profile(data_dir, "dating", "profile_1")
    add_profile_video(data_dir, "dating", "profile_1")

    response = _get(_client(), f"{_PREFIX}/dating/profile_1")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "dating/profile_1"
    assert body["stock"] == 1
    assert body["preview_url"] == _PREFIX + "/dating/profile_1/preview"


def test_get_missing_profile_returns_404(data_dir: Path) -> None:
    response = _get(_client(), f"{_PREFIX}/dating/missing")

    assert response.status_code == 404
    assert "профиль не найден" in response.json()["detail"]


def test_get_rejects_unsafe_profile_id(data_dir: Path) -> None:
    response = _get(_client(), f"{_PREFIX}/dating%5Cescape/profile_1")

    assert response.status_code == 422
    assert response.json() == {"detail": "unsafe profile id"}


def test_patch_toggles_and_clears_overrides(data_dir: Path) -> None:
    add_profile(data_dir, "dating", "profile_1")
    client = _client()

    enabled = _patch(client, f"{_PREFIX}/dating/profile_1", {"refill_enabled": True})
    assert enabled.status_code == 200
    assert enabled.json()["refill_enabled"] is True
    assert enabled.json()["target"] == 20
    assert enabled.json()["target_override"] is None

    stored = _patch(
        client,
        f"{_PREFIX}/dating/profile_1",
        {"target": 10, "refill_at": 0},
    )
    assert stored.status_code == 200
    assert stored.json()["target"] == 10
    assert stored.json()["refill_at"] == 0
    assert stored.json()["target_override"] == 10
    assert stored.json()["refill_at_override"] == 0

    cleared_target = _patch(client, f"{_PREFIX}/dating/profile_1", {"target": None})
    assert cleared_target.status_code == 200
    assert cleared_target.json()["target"] == 20
    assert cleared_target.json()["target_override"] is None
    assert cleared_target.json()["refill_at"] == 0
    assert cleared_target.json()["refill_at_override"] == 0

    cleared_refill = _patch(client, f"{_PREFIX}/dating/profile_1", {"refill_at": None})
    assert cleared_refill.status_code == 200
    assert cleared_refill.json()["refill_at"] == 2
    assert cleared_refill.json()["refill_at_override"] is None
    assert cleared_refill.json()["refill_enabled"] is True


def test_patch_rejects_refill_at_not_less_than_target(data_dir: Path) -> None:
    add_profile(data_dir, "dating", "profile_1")
    client = _client()
    path = f"{_PREFIX}/dating/profile_1"

    assert _patch(client, path, {"target": 1, "refill_at": 1}).status_code == 422
    assert _patch(client, path, {"target": 1}).status_code == 422
    valid = _patch(client, path, {"target": 1, "refill_at": 0})
    assert valid.status_code == 200
    assert valid.json()["target"] == 1
    assert valid.json()["refill_at"] == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"target": 0},
        {"target": 1001},
        {"refill_at": -1},
        {"refill_enabled": None},
        {"unknown": 1},
    ],
)
def test_patch_rejects_invalid_body(data_dir: Path, payload: dict[str, object]) -> None:
    add_profile(data_dir, "dating", "profile_1")

    response = _patch(_client(), f"{_PREFIX}/dating/profile_1", payload)

    assert response.status_code == 422


def test_patch_missing_profile_returns_404(data_dir: Path) -> None:
    response = _patch(_client(), f"{_PREFIX}/dating/missing", {"refill_enabled": True})

    assert response.status_code == 404


def test_patch_storage_failure_returns_500(data_dir: Path) -> None:
    add_profile(data_dir, "dating", "profile_1")
    db_path = data_dir / DB_DIR / "db.sqlite3"
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"not a database")

    response = _patch(_client(), f"{_PREFIX}/dating/profile_1", {"refill_enabled": True})

    assert response.status_code == 500
    assert response.json() == {"detail": "profile settings write failed"}


def test_preview_returns_jpeg_and_reuses_cache(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_profile(data_dir, "dating", "profile_1")
    add_profile_video(data_dir, "dating", "profile_1", mtime=1_000)
    calls: list[Path] = []
    monkeypatch.setattr(preview, "extract_preview", _fake_extract(calls))
    client = _client()

    first = _get(client, f"{_PREFIX}/dating/profile_1/preview")

    assert first.status_code == 200
    assert first.headers["Content-Type"] == "image/jpeg"
    assert first.headers["Cache-Control"] == "max-age=60"
    assert first.content == b"\xff\xd8frame"
    assert (data_dir / PREVIEW_DIR / "dating_profile_1.jpg").is_file()
    assert len(calls) == 1

    second = _get(client, f"{_PREFIX}/dating/profile_1/preview")

    assert second.status_code == 200
    assert len(calls) == 1


def test_preview_rebuilds_when_source_is_newer(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_profile(data_dir, "dating", "profile_1")
    source = add_profile_video(data_dir, "dating", "profile_1", mtime=1_000)
    calls: list[Path] = []
    monkeypatch.setattr(preview, "extract_preview", _fake_extract(calls))
    client = _client()
    assert _get(client, f"{_PREFIX}/dating/profile_1/preview").status_code == 200
    assert len(calls) == 1

    future = time.time() + 10
    os.utime(source, (future, future))
    rebuilt = _get(client, f"{_PREFIX}/dating/profile_1/preview")

    assert rebuilt.status_code == 200
    assert len(calls) == 2


def test_preview_without_video_returns_404(data_dir: Path) -> None:
    add_profile(data_dir, "dating", "profile_1")

    response = _get(_client(), f"{_PREFIX}/dating/profile_1/preview")

    assert response.status_code == 404
    assert response.json() == {"detail": "preview not found"}


def test_preview_missing_profile_returns_404(data_dir: Path) -> None:
    response = _get(_client(), f"{_PREFIX}/dating/missing/preview")

    assert response.status_code == 404


def test_preview_failure_returns_503_without_stderr(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    log_records: list[dict[str, Any]],
) -> None:
    add_profile(data_dir, "dating", "profile_1")
    add_profile_video(data_dir, "dating", "profile_1", mtime=1_000)

    def _fail(_source: Path, _target: Path) -> None:
        raise subprocess.CalledProcessError(1, ["ffmpeg"], stderr=b"secret stderr")

    monkeypatch.setattr(preview, "extract_preview", _fail)

    response = _get(_client(), f"{_PREFIX}/dating/profile_1/preview")

    assert response.status_code == 503
    assert response.json() == {"detail": "preview unavailable"}
    assert "secret stderr" not in response.text
    records = [record for record in log_records if record["function"] == "profile_preview"]
    assert records[-1]["extra"]["stderr"] == "secret stderr"


@pytest.mark.parametrize(
    "path",
    [
        _PREFIX,
        f"{_PREFIX}/dating/profile_1",
        f"{_PREFIX}/dating/profile_1/preview",
    ],
)
def test_profile_routes_require_token(data_dir: Path, path: str) -> None:
    response = _get(_client(), path, token=None)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_patch_requires_token(data_dir: Path) -> None:
    response = _patch(
        _client(),
        f"{_PREFIX}/dating/profile_1",
        {"refill_enabled": True},
        token=None,
    )

    assert response.status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        _PREFIX,
        f"{_PREFIX}/dating/profile_1",
        f"{_PREFIX}/dating/profile_1/preview",
    ],
)
def test_profile_routes_without_configured_token_return_503(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    monkeypatch.delenv("CONTENT_FACTORY_DELIVERY_TOKEN")

    response = _get(_client(), path)

    assert response.status_code == 503
