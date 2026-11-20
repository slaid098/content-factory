from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from content_factory.api.app import create_app
from content_factory.constants import ARCHIVE_DIR, CONTENT_DIR
from content_factory.exceptions import DeliveryError
from content_factory.services.delivery.scheduler import (
    ARCHIVE_CLEANUP_JOB_ID,
    create_cleanup_scheduler,
)
from content_factory.services.delivery.service import cleanup_expired_archives
from delivery_helpers import REQUEST_ID, REQUEST_ID_TEXT, add_downloaded_video, touch_mp4

_TOKEN = "delivery-secret"
_MOMENT = datetime(2026, 5, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point the app at a temporary data directory and configure a token."""
    monkeypatch.delenv("CONTENT_FACTORY_DATA_DIR", raising=False)
    monkeypatch.delenv("CONTENT_FACTORY_DELIVERY_TOKEN", raising=False)
    monkeypatch.delenv("CONTENT_FACTORY_DELIVERY_RETENTION_HOURS", raising=False)
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CONTENT_FACTORY_DELIVERY_TOKEN", _TOKEN)
    return tmp_path


def _client() -> TestClient:
    return TestClient(create_app())


def _body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "language": "ru",
        "count": 1,
        "request_id": REQUEST_ID_TEXT,
    }
    body.update(overrides)
    return body


def _post(
    client: TestClient,
    *,
    token: str | None = _TOKEN,
    **overrides: object,
) -> httpx.Response:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.post("/api/v1/content/deliveries", json=_body(**overrides), headers=headers)


def test_delivery_without_configured_token_returns_503(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CONTENT_FACTORY_DELIVERY_TOKEN")

    response = _post(_client())

    assert response.status_code == 503
    assert _TOKEN not in response.text


def test_delivery_without_authorization_returns_401(data_dir: Path) -> None:
    response = _post(_client(), token=None)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_delivery_with_wrong_token_returns_401(data_dir: Path) -> None:
    response = _post(_client(), token="wrong")

    assert response.status_code == 401


def test_delivery_with_wrong_scheme_returns_401(data_dir: Path) -> None:
    response = _client().post(
        "/api/v1/content/deliveries",
        json=_body(),
        headers={"Authorization": f"Basic {_TOKEN}"},
    )

    assert response.status_code == 401


def test_delivery_with_empty_bearer_returns_401(data_dir: Path) -> None:
    response = _client().post(
        "/api/v1/content/deliveries",
        json=_body(),
        headers={"Authorization": "Bearer   "},
    )

    assert response.status_code == 401


def test_delivery_returns_204_without_mp4(data_dir: Path) -> None:
    response = _post(_client())

    assert response.status_code == 204
    assert response.content == b""


def test_delivery_returns_zip_with_real_title_and_removes_stock(data_dir: Path) -> None:
    first_hash = "a" * 64
    second_hash = "b" * 64
    first = touch_mp4(data_dir / CONTENT_DIR / "ru" / f"{first_hash}.mp4", b"first")
    second = touch_mp4(data_dir / CONTENT_DIR / "ru" / f"{second_hash}.mp4", b"second")
    add_downloaded_video(data_dir / "db" / "db.sqlite3", first_hash, "Реальное описание")

    response = _post(_client())

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/zip"
    assert response.headers["Content-Disposition"] == (
        f'attachment; filename="delivery-{REQUEST_ID}.zip"'
    )
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["001.mp4", "001.json"]
        assert archive.read("001.mp4") == b"first"
        assert json.loads(archive.read("001.json")) == {"title": "Реальное описание"}
    assert not first.exists()
    assert second.is_file()
    archive_path = data_dir / ARCHIVE_DIR / "ru" / f"{REQUEST_ID}.zip"
    assert archive_path.is_file()

    stock = _client().get("/api/v1/stock").json()
    assert stock["languages"] == [{"language": "ru", "stock": 1, "target": 150, "deficit": 149}]


def test_delivery_is_idempotent_by_request_id(data_dir: Path) -> None:
    directory = data_dir / CONTENT_DIR / "ru"
    touch_mp4(directory / "a.mp4", b"old")
    client = _client()
    first = _post(client)
    assert first.status_code == 200
    touch_mp4(directory / "0.mp4", b"new")

    second = _post(client)

    assert second.status_code == 200
    assert second.content == first.content
    assert (directory / "0.mp4").is_file()
    assert not (directory / "a.mp4").exists()


def test_delivery_uses_canonical_request_id_in_filename(data_dir: Path) -> None:
    touch_mp4(data_dir / CONTENT_DIR / "ru" / "a.mp4")
    uppercase = str(REQUEST_ID).upper()

    response = _post(_client(), request_id=uppercase)

    assert response.status_code == 200
    assert (data_dir / ARCHIVE_DIR / "ru" / f"{REQUEST_ID}.zip").is_file()
    assert response.headers["Content-Disposition"] == (
        f'attachment; filename="delivery-{REQUEST_ID}.zip"'
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"language": "ru/"},
        {"language": "RU"},
        {"language": "r"},
        {"count": 0},
        {"count": 11},
        {"request_id": "not-a-uuid"},
    ],
)
def test_delivery_rejects_invalid_body(data_dir: Path, overrides: dict[str, object]) -> None:
    response = _post(_client(), **overrides)

    assert response.status_code == 422


def test_delivery_returns_500_when_archive_cannot_be_built(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touch_mp4(data_dir / CONTENT_DIR / "ru" / "a.mp4")

    def _fail(*args: object, **kwargs: object) -> None:
        raise DeliveryError("boom")

    monkeypatch.setattr("content_factory.api.v1.routes.content.deliver", _fail)

    response = _post(_client())

    assert response.status_code == 500


def test_cleanup_scheduler_contract(tmp_path: Path) -> None:
    scheduler = create_cleanup_scheduler(tmp_path, 24)

    job = scheduler.get_job(ARCHIVE_CLEANUP_JOB_ID)

    assert job is not None
    assert job.func is cleanup_expired_archives
    assert job.args == (tmp_path, 24)
    assert job.max_instances == 1
    assert job.coalesce is True
    assert str(job.trigger) == "cron[minute='0']"


def test_lifespan_runs_startup_cleanup(data_dir: Path) -> None:
    directory = data_dir / ARCHIVE_DIR / "ru"
    directory.mkdir(parents=True)
    expired = directory / "expired.zip"
    fresh = directory / "fresh.zip"
    expired.write_bytes(b"expired")
    fresh.write_bytes(b"fresh")
    stale = (_MOMENT - timedelta(hours=25)).timestamp()
    os.utime(expired, (stale, stale))

    with TestClient(create_app()) as client:
        assert not expired.exists()
        assert fresh.is_file()
        assert _post(client).status_code == 204
