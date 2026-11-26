"""Profile API: listing, refill settings and preview."""

from __future__ import annotations

import os
import sqlite3
import stat
import subprocess
import time
from dataclasses import fields
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient
from tests.unit.common.profile_helpers import (
    add_profile,
    add_profile_photo,
    add_profile_video,
    set_profile_refill,
    write_run_report,
)
from tests.unit.generator.helpers import make_profile

from content_factory.api.app import create_app
from content_factory.constants import CONFIG_DIR, DB_DIR, PREVIEW_DIR, PROFILES_DIR
from content_factory.services.generator.profiles import _OverlayStyle
from content_factory.services.profiles import preview
from content_factory.utils import prompts

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


def _put(
    client: TestClient,
    path: str,
    payload: dict[str, object],
    *,
    token: str | None = _TOKEN,
) -> httpx.Response:
    return client.put(path, json=payload, headers=_headers(token))


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
    add_profile_photo(data_dir, "dating", "profile_1")
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
        "title": None,
        "summary": None,
        "width": 544,
        "height": 960,
        "audio": True,
        "stock": 1,
        "photo_stock": 0,
        "target": 20,
        "refill_at": 2,
        "refill_enabled": False,
        "target_override": None,
        "refill_at_override": None,
        "photo_target": 0,
        "photo_refill_at": 0,
        "preview_url": "/api/v1/content/profiles/auto/profile_2/preview",
        "last_run": None,
        "outputs": ["video"],
    }
    assert second["stock"] == 2
    assert second["photo_stock"] == 1
    assert second["audio"] is False
    assert second["target"] == 5
    assert second["refill_at"] == 1
    assert second["refill_enabled"] is True
    assert second["target_override"] == 5
    assert second["refill_at_override"] == 1
    assert second["photo_target"] == 0
    assert second["photo_refill_at"] == 0
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


def test_list_returns_profile_title_and_summary(data_dir: Path) -> None:
    add_profile(
        data_dir,
        "dating",
        "profile_1",
        title="Знакомства для 30+",
        summary="Спокойные знакомства с акцентом на серьёзные отношения.",
    )

    response = _get(_client(), _PREFIX)

    assert response.status_code == 200
    profile = response.json()["profiles"][0]
    assert profile["title"] == "Знакомства для 30+"
    assert profile["summary"] == "Спокойные знакомства с акцентом на серьёзные отношения."


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
    assert response.json()["profiles"][0]["photo_stock"] == 0
    assert response.json()["profiles"][0]["target"] == 20


def test_list_returns_500_on_broken_runtime_config(data_dir: Path) -> None:
    config = data_dir / CONFIG_DIR / "config.yaml"
    config.parent.mkdir(parents=True)
    config.write_text("generator: [\n", encoding="utf-8")

    response = _get(_client(), _PREFIX)

    assert response.status_code == 500
    assert response.json() == {"detail": "profiles unavailable"}


def test_list_skips_non_utf8_run_report(
    data_dir: Path,
    log_records: list[dict[str, Any]],
) -> None:
    add_profile(data_dir, "dating", "profile_1")
    report = write_run_report(data_dir / "generated", generated=1)
    report.write_bytes(b'{"run_id": "\xff"}')

    response = _get(_client(), _PREFIX)

    assert response.status_code == 200
    assert response.json()["profiles"][0]["last_run"] is None
    records = [record for record in log_records if record["function"] == "_read"]
    assert records[-1]["level"].name == "WARNING"
    assert records[-1]["extra"]["path"] == str(report)


def test_get_returns_one_profile(data_dir: Path) -> None:
    _full_profile(data_dir)
    add_profile_video(data_dir, "dating", "profile_1")
    add_profile_photo(data_dir, "dating", "profile_1")

    response = _get(_client(), f"{_PREFIX}/dating/profile_1")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "dating/profile_1"
    assert body["stock"] == 1
    assert body["photo_stock"] == 1
    assert body["preview_url"] == _PREFIX + "/dating/profile_1/preview"


def test_get_missing_profile_returns_404(data_dir: Path) -> None:
    response = _get(_client(), f"{_PREFIX}/dating/missing")

    assert response.status_code == 404
    assert response.json() == {"detail": "profile not found"}


def test_get_broken_profile_hides_path_and_yaml(data_dir: Path) -> None:
    directory = add_profile(data_dir, "dating", "profile_1")
    (directory / "profile.yaml").write_text("width: [\nsecret: \xff\n", encoding="utf-8")

    response = _get(_client(), f"{_PREFIX}/dating/profile_1")

    assert response.status_code == 404
    assert response.json() == {"detail": "profile not found"}
    assert str(data_dir) not in response.text
    assert "profile.yaml" not in response.text
    assert "secret" not in response.text


def test_get_shared_profile_returns_404(data_dir: Path) -> None:
    fonts = data_dir / "profiles" / "_shared" / "fonts"
    fonts.mkdir(parents=True)
    (fonts / "font.ttf").write_bytes(b"font")

    response = _get(_client(), f"{_PREFIX}/_shared/fonts")

    assert response.status_code == 404
    assert response.json() == {"detail": "profile not found"}
    assert str(data_dir) not in response.text


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


def test_patch_stores_photo_target_and_threshold(data_dir: Path) -> None:
    _full_profile(data_dir)
    client = _client()
    path = f"{_PREFIX}/dating/profile_1"

    response = _patch(client, path, {"photo_target": 10, "photo_refill_at": 4})

    assert response.status_code == 200
    assert response.json()["photo_target"] == 10
    assert response.json()["photo_refill_at"] == 4
    stored = _get(client, path).json()
    assert stored["photo_target"] == 10
    assert stored["photo_refill_at"] == 4
    assert stored["target"] == 20
    assert stored["refill_at"] == 2


def test_patch_disables_photo_refill_with_a_zero_pair(data_dir: Path) -> None:
    add_profile(data_dir, "dating", "profile_1")
    client = _client()
    path = f"{_PREFIX}/dating/profile_1"
    assert _patch(client, path, {"photo_target": 10, "photo_refill_at": 4}).status_code == 200

    rejected = _patch(client, path, {"photo_target": 0})

    assert rejected.status_code == 422
    disabled = _patch(client, path, {"photo_target": 0, "photo_refill_at": 0})
    assert disabled.status_code == 200
    assert disabled.json()["photo_target"] == 0
    assert disabled.json()["photo_refill_at"] == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"photo_target": 0, "photo_refill_at": 1},
        {"photo_target": 5, "photo_refill_at": 5},
        {"photo_target": 5, "photo_refill_at": 6},
        {"photo_target": -1},
        {"photo_target": 1001},
        {"photo_refill_at": 1000},
        {"photo_target": None},
        {"photo_refill_at": None},
    ],
)
def test_patch_rejects_invalid_photo_settings(
    data_dir: Path,
    payload: dict[str, object],
) -> None:
    add_profile(data_dir, "dating", "profile_1")

    response = _patch(_client(), f"{_PREFIX}/dating/profile_1", payload)

    assert response.status_code == 422


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
    assert first.headers["X-Content-Type-Options"] == "nosniff"
    assert first.content == b"\xff\xd8frame"
    assert (data_dir / PREVIEW_DIR / "dating" / "profile_1.jpg").is_file()
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


@pytest.mark.parametrize(
    ("suffix", "media_type"),
    [
        (".jpg", "image/jpeg"),
        (".jpeg", "image/jpeg"),
        (".png", "image/png"),
        (".webp", "image/webp"),
    ],
)
def test_preview_returns_newest_photo_with_real_media_type(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    media_type: str,
) -> None:
    add_profile(data_dir, "dating", "profile_1")
    add_profile_photo(
        data_dir,
        "dating",
        "profile_1",
        filename="dating_profile_1_old.jpg",
        mtime=1_000,
    )
    add_profile_photo(
        data_dir,
        "dating",
        "profile_1",
        filename=f"dating_profile_1_new{suffix}",
        data=b"image",
        mtime=2_000,
    )
    calls: list[Path] = []
    monkeypatch.setattr(preview, "extract_preview", _fake_extract(calls))

    response = _get(_client(), f"{_PREFIX}/dating/profile_1/preview")

    assert response.status_code == 200
    assert response.headers["Content-Type"] == media_type
    assert response.headers["Cache-Control"] == "max-age=60"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.content == b"image"
    assert calls == []
    assert not (data_dir / PREVIEW_DIR).exists()


def test_preview_prefers_video_over_a_newer_photo(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_profile(data_dir, "dating", "profile_1")
    video = add_profile_video(data_dir, "dating", "profile_1", mtime=1_000)
    add_profile_photo(data_dir, "dating", "profile_1", mtime=3_000)
    calls: list[Path] = []
    monkeypatch.setattr(preview, "extract_preview", _fake_extract(calls))

    response = _get(_client(), f"{_PREFIX}/dating/profile_1/preview")

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/jpeg"
    assert response.content == b"\xff\xd8frame"
    assert calls == [video]


def test_preview_serves_photo_without_sidecar(data_dir: Path) -> None:
    add_profile(data_dir, "dating", "profile_1")
    add_profile_photo(data_dir, "dating", "profile_1", data=b"image", sidecar=False)

    response = _get(_client(), f"{_PREFIX}/dating/profile_1/preview")

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "image/jpeg"
    assert response.content == b"image"


def test_preview_without_media_returns_404(data_dir: Path) -> None:
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
    add_profile_photo(data_dir, "dating", "profile_1", mtime=2_000)

    def _fail(_source: Path, _target: Path) -> None:
        raise subprocess.CalledProcessError(1, ["ffmpeg"], stderr=b"secret stderr")

    monkeypatch.setattr(preview, "extract_preview", _fail)

    response = _get(_client(), f"{_PREFIX}/dating/profile_1/preview")

    assert response.status_code == 503
    assert response.json() == {"detail": "preview unavailable"}
    assert "secret stderr" not in response.text
    records = [record for record in log_records if record["function"] == "_video_preview"]
    assert records[-1]["extra"]["stderr"] == "secret stderr"


@pytest.mark.parametrize(
    "path",
    [
        _PREFIX,
        f"{_PREFIX}/dating/profile_1",
        f"{_PREFIX}/dating/profile_1/preview",
        f"{_PREFIX}/dating/profile_1/prompts",
    ],
)
def test_profile_routes_require_token(data_dir: Path, path: str) -> None:
    response = _get(_client(), path, token=None)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_prompt_writes_require_token(data_dir: Path) -> None:
    client = _client()
    paths = [
        f"{_PREFIX}/dating/profile_1/prompts/template",
        f"{_PREFIX}/dating/profile_1/prompts/cards/c01",
        f"{_PREFIX}/dating/profile_1/prompts/text",
    ]

    for path in paths:
        assert _put(client, path, {"text": "x"}, token=None).status_code == 401


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


def _full_profile(
    data_dir: Path,
    name: str = "profile_1",
    **kwargs: Any,
) -> Path:
    """Create a fully valid profile under the runtime profiles root."""
    return make_profile(data_dir / PROFILES_DIR, f"dating/{name}", **kwargs)


def test_get_detail_returns_outputs_recipe_and_safe_settings(data_dir: Path) -> None:
    _full_profile(
        data_dir,
        width=544,
        height=960,
        extra={
            "frames": 121,
            "frames_jitter": 0,
            "fps": 25,
            "gpu": "RTX 4090",
            "max_cost": 0.35,
            "image": "video-gen:test",
            "production": [
                {"producer": "vast", "type": "video", "qa": ["clef"]},
                {"producer": "card", "type": "photo", "qa": ["image"]},
            ],
        },
    )

    response = _get(_client(), f"{_PREFIX}/dating/profile_1")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "dating/profile_1"
    assert body["outputs"] == ["video", "photo"]
    assert body["production"] == [
        {
            "producer": "vast",
            "type": "video",
            "checks": [{"name": "clef", "stage": "source"}],
        },
        {
            "producer": "card",
            "type": "photo",
            "checks": [{"name": "image", "stage": "final"}],
        },
    ]
    settings = body["settings"]
    assert settings["video"] == {
        "width": 544,
        "height": 960,
        "frames": 121,
        "frames_jitter": 0,
        "fps": 25,
        "renderer": "default",
        "image": "video-gen:test",
        "audio_tracks": 1,
    }
    assert settings["rent"] == {
        "gpu": "RTX 4090",
        "max_cost": 0.35,
        "disk_space": 80,
        "gpu_ram": 30,
        "inet_down": 500,
        "inet_up_cost": 0.004,
        "inet_down_cost": 0.004,
    }
    assert settings["text"]["font"] == "arialbd.ttf"
    assert settings["text"]["negative_prompt"] == "blurry, low quality"
    assert settings["text"]["cfg"] is None
    assert set(settings["text"]["style"]) == {field.name for field in fields(_OverlayStyle)}
    assert settings["text"]["style"]["font_size"] == 0.06
    assert settings["text"]["style"]["text_color"] == "#3B2A20"
    assert settings["delivery"] == {
        "title_mode": "generated",
        "title": None,
        "hashtags": [],
    }
    assert str(data_dir) not in response.text
    assert _TOKEN not in response.text


def test_get_detail_uses_the_registry_recipe_order(data_dir: Path) -> None:
    _full_profile(
        data_dir,
        extra={
            "production": [
                {"producer": "card", "type": "photo", "qa": ["image"]},
                {"producer": "vast", "type": "video", "qa": ["clef"]},
            ]
        },
    )

    body = _get(_client(), f"{_PREFIX}/dating/profile_1").json()

    assert body["outputs"] == ["video", "photo"]
    assert [step["producer"] for step in body["production"]] == ["card", "vast"]


def test_prompts_are_stable_and_have_no_photo_without_a_photo_layer(
    data_dir: Path,
) -> None:
    _full_profile(data_dir)
    client = _client()
    path = f"{_PREFIX}/dating/profile_1/prompts"

    first = _get(client, path)
    second = _get(client, path)

    assert first.status_code == 200
    assert first.json() == second.json()
    body = first.json()
    assert body["template"]["filename"] == "prompt_template.txt"
    assert "{age}" in body["template"]["text"]
    assert [card["id"] for card in body["cards"]] == ["c01", "c02", "c03"]
    assert all(card["overridden"] is False for card in body["cards"])
    assert all("{" not in card["prompt"] for card in body["cards"])
    assert body["cards"][0]["values"]["hair_color_text"] == "dark brown"
    assert body["photo"] is None


def test_prompts_return_the_photo_text_only_with_a_photo_layer(data_dir: Path) -> None:
    _full_profile(
        data_dir,
        extra={
            "production": [
                {"producer": "vast", "type": "video", "qa": ["clef"]},
                {"producer": "card", "type": "photo", "qa": ["image"]},
            ]
        },
    )

    body = _get(_client(), f"{_PREFIX}/dating/profile_1/prompts").json()

    assert body["photo"] == {
        "filename": "text_prompt.txt",
        "text": "Сгенерируй текст карточки.",
    }


def test_prompts_use_the_legacy_template_file_when_the_new_one_is_absent(
    data_dir: Path,
) -> None:
    directory = _full_profile(data_dir)
    (directory / "prompt_template.txt").unlink()
    (directory / "video_prompt.txt").write_text("legacy {age}", encoding="utf-8")

    body = _get(_client(), f"{_PREFIX}/dating/profile_1/prompts").json()

    assert body["template"] == {"filename": "video_prompt.txt", "text": "legacy {age}"}


def test_put_template_writes_the_used_file_and_keeps_its_mode(data_dir: Path) -> None:
    directory = _full_profile(data_dir)
    path = directory / "prompt_template.txt"
    path.chmod(0o640)
    client = _client()

    response = _put(
        client,
        f"{_PREFIX}/dating/profile_1/prompts/template",
        {"text": "new scene {age}"},
    )

    assert response.status_code == 200
    assert response.json() == {"filename": "prompt_template.txt", "text": "new scene {age}"}
    assert path.read_text(encoding="utf-8") == "new scene {age}"
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert list(directory.glob(".prompt_template.txt.*.tmp")) == []
    prompts_body = _get(client, f"{_PREFIX}/dating/profile_1/prompts").json()
    assert prompts_body["template"]["text"] == "new scene {age}"


def test_put_template_updates_the_legacy_file_when_it_is_the_used_one(
    data_dir: Path,
) -> None:
    directory = _full_profile(data_dir)
    (directory / "prompt_template.txt").unlink()
    legacy = directory / "video_prompt.txt"
    legacy.write_text("legacy {age}", encoding="utf-8")

    response = _put(
        _client(),
        f"{_PREFIX}/dating/profile_1/prompts/template",
        {"text": "updated legacy"},
    )

    assert response.status_code == 200
    assert response.json() == {"filename": "video_prompt.txt", "text": "updated legacy"}
    assert legacy.read_text(encoding="utf-8") == "updated legacy"


@pytest.mark.parametrize("text", ["", "   ", "x" * 20_001])
def test_put_template_rejects_invalid_text(data_dir: Path, text: str) -> None:
    directory = _full_profile(data_dir)
    path = directory / "prompt_template.txt"
    original = path.read_text(encoding="utf-8")

    response = _put(
        _client(),
        f"{_PREFIX}/dating/profile_1/prompts/template",
        {"text": text},
    )

    assert response.status_code == 422
    assert path.read_text(encoding="utf-8") == original


def test_put_card_stores_an_override_without_rewriting_looks(data_dir: Path) -> None:
    directory = _full_profile(data_dir)
    looks_path = directory / "looks.yaml"
    looks_before = looks_path.read_bytes()
    client = _client()

    response = _put(
        client,
        f"{_PREFIX}/dating/profile_1/prompts/cards/c01",
        {"text": "custom {age} {day|evening}"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "id": "c01",
        "prompt": "custom {age} {day|evening}",
        "overridden": True,
    }
    assert looks_path.read_bytes() == looks_before
    overrides = yaml.safe_load((directory / prompts.OVERRIDES_FILENAME).read_text(encoding="utf-8"))
    assert overrides == {"prompts": {"c01": "custom {age} {day|evening}"}}
    body = _get(client, f"{_PREFIX}/dating/profile_1/prompts").json()
    assert body["cards"][0]["overridden"] is True
    assert body["cards"][0]["prompt"] == "custom {age} {day|evening}"
    assert body["cards"][1]["overridden"] is False


def test_put_card_with_empty_text_removes_the_override(data_dir: Path) -> None:
    directory = _full_profile(data_dir)
    client = _client()
    path = f"{_PREFIX}/dating/profile_1/prompts/cards/c01"
    assert _put(client, path, {"text": "custom"}).status_code == 200

    cleared = _put(client, path, {"text": "   "})

    assert cleared.status_code == 200
    assert cleared.json()["id"] == "c01"
    assert cleared.json()["overridden"] is False
    assert cleared.json()["prompt"] != "custom"
    assert "{" not in cleared.json()["prompt"]
    overrides = yaml.safe_load((directory / prompts.OVERRIDES_FILENAME).read_text(encoding="utf-8"))
    assert overrides == {"prompts": {}}


def test_put_card_unknown_look_returns_404_and_writes_nothing(data_dir: Path) -> None:
    directory = _full_profile(data_dir)

    response = _put(
        _client(),
        f"{_PREFIX}/dating/profile_1/prompts/cards/c99",
        {"text": "custom"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "profile not found"}
    assert not (directory / prompts.OVERRIDES_FILENAME).exists()


def test_put_text_writes_the_photo_prompt_and_keeps_its_mode(data_dir: Path) -> None:
    directory = _full_profile(
        data_dir,
        extra={
            "production": [
                {"producer": "vast", "type": "video", "qa": ["clef"]},
                {"producer": "card", "type": "photo", "qa": ["image"]},
            ]
        },
    )
    path = directory / "text_prompt.txt"
    path.chmod(0o640)

    response = _put(
        _client(),
        f"{_PREFIX}/dating/profile_1/prompts/text",
        {"text": "Новый текст карточки."},
    )

    assert response.status_code == 200
    assert response.json() == {
        "filename": "text_prompt.txt",
        "text": "Новый текст карточки.",
    }
    assert path.read_text(encoding="utf-8") == "Новый текст карточки."
    assert stat.S_IMODE(path.stat().st_mode) == 0o640


def test_put_text_is_missing_for_a_video_only_profile(data_dir: Path) -> None:
    _full_profile(data_dir)

    response = _put(
        _client(),
        f"{_PREFIX}/dating/profile_1/prompts/text",
        {"text": "Новый текст карточки."},
    )

    assert response.status_code == 404


@pytest.mark.parametrize("text", ["", "  ", "x" * 20_001])
def test_put_text_rejects_invalid_text(data_dir: Path, text: str) -> None:
    _full_profile(
        data_dir,
        extra={
            "production": [
                {"producer": "vast", "type": "video", "qa": ["clef"]},
                {"producer": "card", "type": "photo", "qa": ["image"]},
            ]
        },
    )

    response = _put(
        _client(),
        f"{_PREFIX}/dating/profile_1/prompts/text",
        {"text": text},
    )

    assert response.status_code == 422
