from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

from content_factory.api.app import create_app
from content_factory.constants import ARCHIVE_DIR, CONFIG_DIR, CONTENT_DIR
from content_factory.exceptions import DeliveryError
from content_factory.schemas.content.delivery import DeliveryJobInfo
from content_factory.services.delivery.scheduler import (
    ARCHIVE_CLEANUP_JOB_ID,
    create_cleanup_scheduler,
)
from content_factory.services.delivery.service import cleanup_expired_archives
from content_factory.services.delivery.sidecars import save_job
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


def _headers(token: str | None = _TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token is not None else {}


def _get(
    client: TestClient,
    path: str,
    *,
    token: str | None = _TOKEN,
) -> httpx.Response:
    return client.get(path, headers=_headers(token))


def _touch_archive(
    data_dir: Path,
    request_id: UUID,
    data: bytes,
    created_at: datetime,
    *,
    language: str = "ru",
) -> Path:
    path = data_dir / ARCHIVE_DIR / language / f"{request_id}.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (created_at.timestamp(), created_at.timestamp()))
    return path


def _body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "language": "ru",
        "count": 1,
        "request_id": REQUEST_ID_TEXT,
    }
    body.update(overrides)
    return body


def _write_config(data_dir: Path, text: str) -> None:
    path = data_dir / CONFIG_DIR / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _sidecar(
    data_dir: Path,
    *,
    status: str,
    url: str | None = None,
    size: int = 10,
    files: int = 1,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> DeliveryJobInfo:
    moment = created_at or datetime.now(UTC)
    job = DeliveryJobInfo(
        request_id=REQUEST_ID,
        language="ru",
        status=status,  # type: ignore[arg-type]
        created_at=moment,
        expires_at=expires_at or moment + timedelta(hours=24),
        size=size,
        files=files,
        url=url,
    )
    save_job(data_dir, job)
    return job


def _post(
    client: TestClient,
    *,
    token: str | None = _TOKEN,
    **overrides: object,
) -> httpx.Response:
    return client.post(
        "/api/v1/content/deliveries",
        json=_body(**overrides),
        headers=_headers(token),
    )


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
    assert stock["languages"] == [
        {
            "language": "ru",
            "stock": 1,
            "target": 150,
            "deficit": 149,
            "titled": 0,
            "untitled": 1,
            "video_bytes": 6,
        }
    ]


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
        {"count": 301},
        {"request_id": "not-a-uuid"},
        {"query": ""},
        {"query": "x" * 201},
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


def test_delivery_rejects_count_above_default_max_count(data_dir: Path) -> None:
    response = _post(_client(), count=101)

    assert response.status_code == 422


def test_delivery_accepts_count_300_with_configured_limit(data_dir: Path) -> None:
    _write_config(data_dir, "delivery:\n  max_count: 300\n")

    response = _post(_client(), count=300)

    assert response.status_code == 204


def test_delivery_rejects_count_above_configured_limit(data_dir: Path) -> None:
    _write_config(data_dir, "delivery:\n  max_count: 5\n")

    response = _post(_client(), count=6)

    assert response.status_code == 422


def test_delivery_link_without_configured_token_returns_503(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CONTENT_FACTORY_DELIVERY_TOKEN")

    response = _client().post("/api/v1/content/deliveries/link", json=_body())

    assert response.status_code == 503


def test_delivery_link_without_authorization_returns_401(data_dir: Path) -> None:
    response = _client().post("/api/v1/content/deliveries/link", json=_body())

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_delivery_link_rejects_count_above_limit(data_dir: Path) -> None:
    response = _client().post(
        "/api/v1/content/deliveries/link",
        json=_body(count=101),
        headers=_headers(),
    )

    assert response.status_code == 422


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


_CONTENT_READ_ROUTES = [
    "/api/v1/content/topics?language=ru",
    "/api/v1/content/archives",
    f"/api/v1/content/archives/ru/{REQUEST_ID}.zip",
    f"/api/v1/content/deliveries/ru/{REQUEST_ID}",
]


@pytest.mark.parametrize("path", _CONTENT_READ_ROUTES)
def test_content_read_routes_require_token(data_dir: Path, path: str) -> None:
    response = _get(_client(), path, token=None)

    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


@pytest.mark.parametrize("path", _CONTENT_READ_ROUTES)
def test_content_read_routes_without_configured_token_return_503(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
) -> None:
    monkeypatch.delenv("CONTENT_FACTORY_DELIVERY_TOKEN")

    response = _get(_client(), path)

    assert response.status_code == 503


def test_topics_returns_stock_breakdown(data_dir: Path) -> None:
    db_path = data_dir / "db" / "db.sqlite3"
    directory = data_dir / CONTENT_DIR / "ru"
    first_hash = "a" * 64
    second_hash = "b" * 64
    other_hash = "c" * 64
    touch_mp4(directory / f"{first_hash}.mp4")
    touch_mp4(directory / f"{second_hash}.mp4")
    touch_mp4(directory / f"{other_hash}.mp4")
    add_downloaded_video(db_path, first_hash, "Первое", query="курятник")
    add_downloaded_video(db_path, second_hash, "Второе", query="курятник")
    add_downloaded_video(db_path, other_hash, "Третье", query="заправка")
    touch_mp4(directory / ("d" * 64 + ".mp4"))

    response = _get(_client(), "/api/v1/content/topics?language=ru")

    assert response.status_code == 200
    assert response.json() == {
        "language": "ru",
        "topics": [
            {"query": "заправка", "count": 1},
            {"query": "курятник", "count": 2},
        ],
        "unclassified": 1,
    }


def test_topics_without_stock_returns_empty_breakdown(data_dir: Path) -> None:
    response = _get(_client(), "/api/v1/content/topics?language=en")

    assert response.status_code == 200
    assert response.json() == {"language": "en", "topics": [], "unclassified": 0}


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/content/topics?language=RU",
        "/api/v1/content/topics?language=r",
        "/api/v1/content/topics?language=ru%2F..",
        "/api/v1/content/archives/RU/" + REQUEST_ID_TEXT + ".zip",
        "/api/v1/content/archives/ru/not-a-uuid.zip",
    ],
)
def test_content_read_routes_reject_invalid_path_values(
    data_dir: Path,
    path: str,
) -> None:
    response = _get(_client(), path)

    assert response.status_code == 422


def test_archive_download_path_traversal_returns_404(data_dir: Path) -> None:
    response = _get(
        _client(),
        "/api/v1/content/archives/ru/%2E%2E%2F%2E%2E%2Fdb%2Fdb.sqlite3.zip",
    )

    assert response.status_code in {404, 422}
    assert response.status_code != 200


def test_delivery_with_query_returns_only_matching_file(data_dir: Path) -> None:
    first_hash = "a" * 64
    second_hash = "b" * 64
    first = touch_mp4(data_dir / CONTENT_DIR / "ru" / f"{first_hash}.mp4", b"first")
    second = touch_mp4(data_dir / CONTENT_DIR / "ru" / f"{second_hash}.mp4", b"second")
    add_downloaded_video(
        data_dir / "db" / "db.sqlite3",
        first_hash,
        "Первое",
        query="курятник",
    )
    add_downloaded_video(
        data_dir / "db" / "db.sqlite3",
        second_hash,
        "Второе",
        query="заправка",
    )

    response = _post(_client(), query="курятник")

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["001.mp4", "001.json"]
        assert archive.read("001.mp4") == b"first"
    assert not first.exists()
    assert second.is_file()


def test_delivery_with_query_without_matches_returns_204(data_dir: Path) -> None:
    sha256 = "a" * 64
    source = touch_mp4(data_dir / CONTENT_DIR / "ru" / f"{sha256}.mp4")
    add_downloaded_video(
        data_dir / "db" / "db.sqlite3",
        sha256,
        "Описание",
        query="заправка",
    )

    response = _post(_client(), query="курятник")

    assert response.status_code == 204
    assert source.is_file()


def test_delivery_with_query_and_broken_database_returns_204(data_dir: Path) -> None:
    source = touch_mp4(data_dir / CONTENT_DIR / "ru" / ("a" * 64 + ".mp4"))
    db_path = data_dir / "db" / "db.sqlite3"
    db_path.parent.mkdir(parents=True)
    db_path.write_bytes(b"not a database")

    response = _post(_client(), query="курятник")

    assert response.status_code == 204
    assert source.is_file()


def test_archives_lists_created_archive_with_expiry(data_dir: Path) -> None:
    touch_mp4(data_dir / CONTENT_DIR / "ru" / ("a" * 64 + ".mp4"))
    assert _post(_client()).status_code == 200
    archive_path = data_dir / ARCHIVE_DIR / "ru" / f"{REQUEST_ID}.zip"

    response = _get(_client(), "/api/v1/content/archives")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    item = body[0]
    assert item["language"] == "ru"
    assert item["request_id"] == REQUEST_ID_TEXT
    assert item["size"] == archive_path.stat().st_size
    created_at = datetime.fromisoformat(item["created_at"])
    expires_at = datetime.fromisoformat(item["expires_at"])
    assert expires_at - created_at == timedelta(hours=24)


def test_archives_hides_expired_archive(data_dir: Path) -> None:
    touch_mp4(data_dir / CONTENT_DIR / "ru" / ("a" * 64 + ".mp4"))
    assert _post(_client()).status_code == 200
    expired_id = UUID(int=2)
    expired = _touch_archive(
        data_dir,
        expired_id,
        b"expired",
        datetime.now(UTC) - timedelta(hours=25),
    )

    response = _get(_client(), "/api/v1/content/archives")

    assert response.status_code == 200
    assert [item["request_id"] for item in response.json()] == [REQUEST_ID_TEXT]
    assert expired.is_file()


def test_archives_without_directory_returns_empty_list(data_dir: Path) -> None:
    response = _get(_client(), "/api/v1/content/archives")

    assert response.status_code == 200
    assert response.json() == []


def test_archives_lists_remote_sidecar_without_zip(data_dir: Path) -> None:
    url = "https://filebin.net/cf-11111111-1111-4111-8111-111111111111/11111111-1111-4111-8111-111111111111.zip"
    _sidecar(data_dir, status="ready", url=url, size=303, files=2)

    response = _get(_client(), "/api/v1/content/archives")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["request_id"] == REQUEST_ID_TEXT
    assert body[0]["storage"] == "remote"
    assert body[0]["url"] == url
    assert body[0]["size"] == 303
    assert body[0]["files"] == 2
    assert _TOKEN not in response.text


def test_archives_lists_legacy_local_archive_as_local(data_dir: Path) -> None:
    _touch_archive(data_dir, REQUEST_ID, b"zip", datetime.now(UTC))

    body = _get(_client(), "/api/v1/content/archives").json()

    assert body[0]["storage"] == "local"
    assert body[0]["url"] is None
    assert body[0]["files"] is None


def test_download_remote_archive_redirects_to_filebin(data_dir: Path) -> None:
    url = "https://filebin.net/cf-11111111-1111-4111-8111-111111111111/11111111-1111-4111-8111-111111111111.zip"
    _sidecar(data_dir, status="ready", url=url, size=303, files=2)

    response = _client().get(
        f"/api/v1/content/archives/ru/{REQUEST_ID}.zip",
        headers=_headers(),
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"] == url


def test_download_ready_local_archive_without_zip_returns_404(data_dir: Path) -> None:
    _sidecar(data_dir, status="ready_local", url=None, size=10, files=1)

    response = _get(
        _client(),
        f"/api/v1/content/archives/ru/{REQUEST_ID}.zip",
    )

    assert response.status_code == 404


def test_archives_lists_same_request_id_in_two_languages(data_dir: Path) -> None:
    now = datetime.now(UTC)
    _touch_archive(data_dir, REQUEST_ID, b"ru", now, language="ru")
    _touch_archive(data_dir, REQUEST_ID, b"en", now, language="en")

    response = _get(_client(), "/api/v1/content/archives")

    assert response.status_code == 200
    body = response.json()
    assert {item["language"] for item in body} == {"en", "ru"}
    assert {item["request_id"] for item in body} == {REQUEST_ID_TEXT}


def test_download_archive_returns_same_bytes(data_dir: Path) -> None:
    touch_mp4(data_dir / CONTENT_DIR / "ru" / ("a" * 64 + ".mp4"), b"video")
    assert _post(_client()).status_code == 200
    archive_path = data_dir / ARCHIVE_DIR / "ru" / f"{REQUEST_ID}.zip"
    stored = archive_path.read_bytes()

    response = _get(
        _client(),
        f"/api/v1/content/archives/ru/{REQUEST_ID}.zip",
    )

    assert response.status_code == 200
    assert response.headers["Content-Type"] == "application/zip"
    assert response.headers["Content-Disposition"] == (
        f'attachment; filename="delivery-{REQUEST_ID}.zip"'
    )
    assert response.content == stored
    assert archive_path.is_file()


def test_download_archive_after_expiry_returns_404(data_dir: Path) -> None:
    touch_mp4(data_dir / CONTENT_DIR / "ru" / ("a" * 64 + ".mp4"))
    assert _post(_client()).status_code == 200
    archive_path = data_dir / ARCHIVE_DIR / "ru" / f"{REQUEST_ID}.zip"
    stale = (datetime.now(UTC) - timedelta(hours=25)).timestamp()
    os.utime(archive_path, (stale, stale))

    response = _get(
        _client(),
        f"/api/v1/content/archives/ru/{REQUEST_ID}.zip",
    )

    assert response.status_code == 404


def test_download_missing_archive_returns_404(data_dir: Path) -> None:
    response = _get(
        _client(),
        f"/api/v1/content/archives/ru/{REQUEST_ID}.zip",
    )

    assert response.status_code == 404


def test_download_archive_with_uppercase_request_id_uses_canonical_name(
    data_dir: Path,
) -> None:
    now = datetime.now(UTC)
    _touch_archive(data_dir, REQUEST_ID, b"zip", now)

    response = _get(
        _client(),
        f"/api/v1/content/archives/ru/{REQUEST_ID_TEXT.upper()}.zip",
    )

    assert response.status_code == 200
    assert response.headers["Content-Disposition"] == (
        f'attachment; filename="delivery-{REQUEST_ID}.zip"'
    )
