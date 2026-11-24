from __future__ import annotations

import json
import multiprocessing
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from loguru import logger
from tests.unit.common.delivery_helpers import REQUEST_ID, REQUEST_ID_TEXT, add_title, touch_mp4
from tests.unit.common.profile_helpers import add_profile, add_profile_video

from content_factory.api.app import create_app
from content_factory.constants import ARCHIVE_DIR, CONTENT_DIR, DB_DIR
from content_factory.db.lock import DELIVERY_BUILD_LOCK, lock_path, locked
from content_factory.exceptions import (
    ArchiveTooLargeError,
    DeliveryBusyError,
    DeliveryError,
    UploadError,
)
from content_factory.schemas.content.delivery import DeliveryJobInfo, DeliveryRequest
from content_factory.services.delivery.jobs import _request_language, start_delivery_job
from content_factory.services.delivery.sidecars import save_job

_TOKEN = "delivery-secret"
_SECOND_ID = UUID("22222222-2222-4222-8222-222222222222")


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


def _body(request_id: UUID = REQUEST_ID, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "language": "ru",
        "count": 1,
        "request_id": str(request_id),
    }
    body.update(overrides)
    return body


def _post(
    client: TestClient,
    request_id: UUID = REQUEST_ID,
    **overrides: object,
) -> httpx.Response:
    return client.post(
        "/api/v1/content/deliveries/link",
        json=_body(request_id, **overrides),
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )


def _wait(
    client: TestClient,
    request_id: str,
    status: str,
    timeout: float = 10.0,
    *,
    language: str = "ru",
) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(
            f"/api/v1/content/deliveries/{language}/{request_id}",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert response.status_code == 200
        body = response.json()
        if body["status"] == status:
            return body
        assert body["status"] == "building", body
        time.sleep(0.01)
    raise AssertionError(f"delivery did not reach {status}")


def _job(
    data_dir: Path,
    *,
    status: str,
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
    size: int = 0,
    files: int = 0,
    url: str | None = None,
    error: str | None = None,
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
        error=error,
    )
    save_job(data_dir, job)
    return job


def test_link_returns_building_then_ready_and_removes_zip(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touch_mp4(data_dir / CONTENT_DIR / "ru" / ("a" * 64 + ".mp4"), b"video")
    add_title(data_dir / "db" / "db.sqlite3", "a" * 64, "Название видео")
    url = "https://filebin.net/cf-11111111-1111-4111-8111-111111111111/11111111-1111-4111-8111-111111111111.zip"
    monkeypatch.setattr(
        "content_factory.services.delivery.jobs.upload_archive",
        lambda _archive: url,
    )

    client = _client()
    response = _post(client)

    assert response.status_code == 202
    body = response.json()
    assert body["request_id"] == REQUEST_ID_TEXT
    assert body["language"] == "ru"
    assert body["status"] == "building"
    assert body["size"] == 0
    assert body["files"] == 0
    assert body["url"] is None
    assert body["error"] is None
    assert "created_at" in body
    assert body["expires_at"] > body["created_at"]
    assert _TOKEN not in response.text

    final = _wait(client, REQUEST_ID_TEXT, "ready")

    assert final["url"] == url
    assert final["files"] == 1
    assert int(final["size"]) > 0
    assert final["error"] is None
    assert not (data_dir / ARCHIVE_DIR / "ru" / f"{REQUEST_ID}.zip").exists()
    sidecar = json.loads(
        (data_dir / ARCHIVE_DIR / "ru" / f"{REQUEST_ID}.json").read_text(encoding="utf-8")
    )
    assert sidecar["status"] == "ready"
    assert sidecar["url"] == url
    assert sidecar["files"] == 1


def test_successful_upload_url_is_not_logged(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touch_mp4(data_dir / CONTENT_DIR / "ru" / ("a" * 64 + ".mp4"), b"video")
    add_title(data_dir / "db" / "db.sqlite3", "a" * 64, "Название видео")
    url = "https://filebin.net/cf-11111111-1111-4111-8111-111111111111/11111111-1111-4111-8111-111111111111.zip"
    monkeypatch.setattr(
        "content_factory.services.delivery.jobs.upload_archive",
        lambda _archive: url,
    )
    messages: list[str] = []
    sink_id = logger.add(lambda message: messages.append(str(message)), level="DEBUG")
    try:
        client = _client()
        assert _post(client).status_code == 202
        _wait(client, REQUEST_ID_TEXT, "ready")
    finally:
        logger.remove(sink_id)

    assert url not in "\n".join(messages)


def test_upload_failure_keeps_zip_and_returns_ready_local(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touch_mp4(data_dir / CONTENT_DIR / "ru" / ("a" * 64 + ".mp4"), b"video")
    add_title(data_dir / "db" / "db.sqlite3", "a" * 64, "Название видео")

    def _fail(_archive: Path) -> str:
        raise UploadError("Filebin недоступен")

    monkeypatch.setattr("content_factory.services.delivery.jobs.upload_archive", _fail)
    client = _client()

    assert _post(client).status_code == 202
    final = _wait(client, REQUEST_ID_TEXT, "ready_local")

    assert final["url"] is None
    assert final["error"] == "Filebin недоступен"
    assert "filebin.net" not in final["error"]
    assert final["files"] == 1
    assert (data_dir / ARCHIVE_DIR / "ru" / f"{REQUEST_ID}.zip").is_file()


def test_upload_too_large_falls_back_to_ready_local(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touch_mp4(data_dir / CONTENT_DIR / "ru" / ("a" * 64 + ".mp4"), b"video")
    add_title(data_dir / "db" / "db.sqlite3", "a" * 64, "Название видео")

    def _too_large(_archive: Path) -> str:
        raise ArchiveTooLargeError("архив больше 1 ГБ")

    monkeypatch.setattr("content_factory.services.delivery.jobs.upload_archive", _too_large)
    client = _client()

    assert _post(client).status_code == 202
    final = _wait(client, REQUEST_ID_TEXT, "ready_local")

    assert final["url"] is None
    assert final["error"] == "архив больше 1 ГБ"
    assert (data_dir / ARCHIVE_DIR / "ru" / f"{REQUEST_ID}.zip").is_file()


def test_large_archive_skips_upload_attempt(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touch_mp4(data_dir / CONTENT_DIR / "ru" / ("a" * 64 + ".mp4"), b"video")
    add_title(data_dir / "db" / "db.sqlite3", "a" * 64, "Название видео")
    monkeypatch.setattr("content_factory.services.delivery.jobs.MAX_ARCHIVE_BYTES", 1)
    called = False

    def _unexpected(_archive: Path) -> str:
        nonlocal called
        called = True
        return "https://filebin.net/cf-11111111-1111-4111-8111-111111111111/11111111-1111-4111-8111-111111111111.zip"

    monkeypatch.setattr("content_factory.services.delivery.jobs.upload_archive", _unexpected)
    client = _client()

    assert _post(client).status_code == 202
    final = _wait(client, REQUEST_ID_TEXT, "ready_local")

    assert called is False
    assert final["error"] == "архив больше 1 ГБ, заливка пропущена"
    assert (data_dir / ARCHIVE_DIR / "ru" / f"{REQUEST_ID}.zip").is_file()


def test_unexpected_upload_error_falls_back_to_ready_local(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touch_mp4(data_dir / CONTENT_DIR / "ru" / ("a" * 64 + ".mp4"), b"video")
    add_title(data_dir / "db" / "db.sqlite3", "a" * 64, "Название видео")

    def _fail(_archive: Path) -> str:
        raise RuntimeError("unexpected")

    monkeypatch.setattr("content_factory.services.delivery.jobs.upload_archive", _fail)
    client = _client()

    assert _post(client).status_code == 202
    final = _wait(client, REQUEST_ID_TEXT, "ready_local")

    assert final["url"] is None
    assert final["error"] == "не удалось загрузить архив на файлообменник"


def test_link_without_stock_returns_empty(data_dir: Path) -> None:
    client = _client()

    assert _post(client).status_code == 202
    final = _wait(client, REQUEST_ID_TEXT, "empty")

    assert final["files"] == 0
    assert final["size"] == 0
    assert final["url"] is None
    assert not (data_dir / ARCHIVE_DIR / "ru" / f"{REQUEST_ID}.zip").exists()


def test_link_with_untitled_stock_returns_empty(data_dir: Path) -> None:
    source = touch_mp4(data_dir / CONTENT_DIR / "ru" / ("a" * 64 + ".mp4"), b"video")
    client = _client()

    assert _post(client).status_code == 202
    final = _wait(client, REQUEST_ID_TEXT, "empty")

    assert final["files"] == 0
    assert final["size"] == 0
    assert final["url"] is None
    assert source.is_file()
    assert not (data_dir / ARCHIVE_DIR / "ru" / f"{REQUEST_ID}.zip").exists()


def test_link_with_whitespace_only_title_returns_empty(data_dir: Path) -> None:
    sha256 = "a" * 64
    source = touch_mp4(data_dir / CONTENT_DIR / "ru" / f"{sha256}.mp4", b"video")
    add_title(data_dir / "db" / "db.sqlite3", sha256, " \t\n ", norm="whitespace-title")
    client = _client()

    assert _post(client).status_code == 202
    final = _wait(client, REQUEST_ID_TEXT, "empty")

    assert final["files"] == 0
    assert final["size"] == 0
    assert final["url"] is None
    assert source.is_file()
    assert not (data_dir / ARCHIVE_DIR / "ru" / f"{REQUEST_ID}.zip").exists()


def test_build_error_returns_failed_job(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    touch_mp4(data_dir / CONTENT_DIR / "ru" / ("a" * 64 + ".mp4"), b"video")
    add_title(data_dir / "db" / "db.sqlite3", "a" * 64, "Название видео")

    def _fail(*args: object, **kwargs: object) -> None:
        raise DeliveryError("boom")

    monkeypatch.setattr("content_factory.services.delivery.jobs.deliver", _fail)
    client = _client()

    assert _post(client).status_code == 202
    final = _wait(client, REQUEST_ID_TEXT, "failed")

    assert final["error"] == "не удалось собрать архив"


@pytest.mark.parametrize("archive_name", ["missing.zip", "broken.zip"])
def test_unreadable_archive_returns_failed_job(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    archive_name: str,
) -> None:
    archive = data_dir / ARCHIVE_DIR / "ru" / archive_name
    if archive_name == "broken.zip":
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(b"not a zip")
    monkeypatch.setattr(
        "content_factory.services.delivery.jobs.deliver",
        lambda *args, **kwargs: archive,
    )
    client = _client()

    assert _post(client).status_code == 202
    final = _wait(client, REQUEST_ID_TEXT, "failed")

    assert final["error"] == "не удалось прочитать собранный архив"


def test_second_request_while_building_returns_409_and_same_id_is_idempotent(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()

    def _blocked(*args: object, **kwargs: object) -> None:
        started.set()
        assert release.wait(10)
        return None

    monkeypatch.setattr("content_factory.services.delivery.jobs.deliver", _blocked)
    client = _client()
    try:
        first = _post(client)
        assert first.status_code == 202
        assert started.wait(10)

        same = _post(client)
        assert same.status_code == 202
        assert same.json()["status"] == "building"

        second = _post(client, request_id=_SECOND_ID)
        assert second.status_code == 409
    finally:
        release.set()
    _wait(client, REQUEST_ID_TEXT, "empty")


@pytest.mark.skipif(os.name != "posix", reason="flock is POSIX-only")
def test_build_slot_is_exclusive_across_processes(data_dir: Path) -> None:
    context = multiprocessing.get_context("fork")
    entered = context.Event()
    release = context.Event()

    def hold() -> None:
        with locked(lock_path(data_dir / DB_DIR, DELIVERY_BUILD_LOCK), timeout=5):
            entered.set()
            release.wait(10)

    process = context.Process(target=hold, daemon=True)
    process.start()
    try:
        assert entered.wait(10)
        with pytest.raises(DeliveryBusyError):
            start_delivery_job(
                data_dir,
                DeliveryRequest(language="ru", count=1, request_id=REQUEST_ID),
                retention_hours=24,
            )
    finally:
        release.set()
        process.join(10)
        if process.is_alive():
            process.kill()
            process.join()

    assert process.exitcode == 0
    assert not (data_dir / ARCHIVE_DIR / "ru" / f"{REQUEST_ID}.json").exists()


def test_unusable_lock_dir_maps_to_json_delivery_error(data_dir: Path) -> None:
    (data_dir / DB_DIR).write_text("not a directory", encoding="utf-8")
    request = DeliveryRequest(language="ru", count=1, request_id=REQUEST_ID)

    with pytest.raises(DeliveryError):
        start_delivery_job(data_dir, request, retention_hours=24)

    response = _post(_client())

    assert response.status_code == 500
    assert response.json() == {"detail": "delivery failed"}


def test_repeated_post_for_ready_job_keeps_stock_and_status(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = data_dir / CONTENT_DIR / "ru"
    touch_mp4(directory / ("a" * 64 + ".mp4"), b"first")
    add_title(data_dir / "db" / "db.sqlite3", "a" * 64, "Название старого видео")
    url = "https://filebin.net/cf-11111111-1111-4111-8111-111111111111/11111111-1111-4111-8111-111111111111.zip"
    monkeypatch.setattr(
        "content_factory.services.delivery.jobs.upload_archive",
        lambda _archive: url,
    )
    client = _client()
    assert _post(client).status_code == 202
    _wait(client, REQUEST_ID_TEXT, "ready")
    fresh = touch_mp4(directory / ("b" * 64 + ".mp4"), b"second")
    add_title(data_dir / "db" / "db.sqlite3", "b" * 64, "Название нового видео")

    repeated = _post(client)

    assert repeated.status_code == 202
    body = repeated.json()
    assert body["status"] == "ready"
    assert body["url"] == url
    assert fresh.is_file()


def test_job_get_after_ttl_returns_404_and_removes_sidecar(data_dir: Path) -> None:
    now = datetime.now(UTC)
    _job(
        data_dir,
        status="ready",
        url="https://filebin.net/cf-11111111-1111-4111-8111-111111111111/11111111-1111-4111-8111-111111111111.zip",
        created_at=now - timedelta(hours=25),
        expires_at=now - timedelta(hours=1),
    )

    response = _client().get(
        f"/api/v1/content/deliveries/ru/{REQUEST_ID_TEXT}",
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )

    assert response.status_code == 404
    assert not (data_dir / ARCHIVE_DIR / "ru" / f"{REQUEST_ID}.json").exists()


def test_job_get_unknown_returns_404(data_dir: Path) -> None:
    response = _client().get(
        f"/api/v1/content/deliveries/ru/{REQUEST_ID_TEXT}",
        headers={"Authorization": f"Bearer {_TOKEN}"},
    )

    assert response.status_code == 404


def test_startup_marks_building_as_failed_and_repeat_rebuilds(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    _job(data_dir, status="building", created_at=now, expires_at=now + timedelta(hours=24))
    touch_mp4(data_dir / CONTENT_DIR / "ru" / ("a" * 64 + ".mp4"), b"video")
    add_title(data_dir / "db" / "db.sqlite3", "a" * 64, "Название видео")
    url = "https://filebin.net/cf-11111111-1111-4111-8111-111111111111/11111111-1111-4111-8111-111111111111.zip"
    monkeypatch.setattr(
        "content_factory.services.delivery.jobs.upload_archive",
        lambda _archive: url,
    )

    with TestClient(create_app()) as client:
        failed = client.get(
            f"/api/v1/content/deliveries/ru/{REQUEST_ID_TEXT}",
            headers={"Authorization": f"Bearer {_TOKEN}"},
        )
        assert failed.status_code == 200
        assert failed.json()["status"] == "failed"
        assert failed.json()["error"] == "сборка прервана перезапуском API"

        assert _post(client).status_code == 202
        final = _wait(client, REQUEST_ID_TEXT, "ready")

    assert final["url"] == url


def test_link_by_profile_resolves_and_stores_registry_language(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add_profile(data_dir, "dating", "profile_1", language="en")
    source = add_profile_video(
        data_dir,
        "dating",
        "profile_1",
        language="en",
        data=b"english",
    )
    add_title(data_dir / "db" / "db.sqlite3", "dating_profile_1_run_001", "English title")
    url = "https://filebin.net/cf-11111111-1111-4111-8111-111111111111/11111111-1111-4111-8111-111111111111.zip"
    monkeypatch.setattr(
        "content_factory.services.delivery.jobs.upload_archive",
        lambda _archive: url,
    )
    client = _client()

    response = _post(client, language=None, profile="dating/profile_1")

    assert response.status_code == 202
    body = response.json()
    assert body["language"] == "en"
    assert body["status"] == "building"
    final = _wait(client, REQUEST_ID_TEXT, "ready", language="en")

    assert final["url"] == url
    assert final["files"] == 1
    assert not source.exists()
    sidecar = data_dir / ARCHIVE_DIR / "en" / f"{REQUEST_ID}.json"
    assert sidecar.is_file()
    assert not (data_dir / ARCHIVE_DIR / "ru" / f"{REQUEST_ID}.json").exists()


def test_link_by_profile_without_stock_returns_empty(data_dir: Path) -> None:
    add_profile(data_dir, "dating", "profile_1", language="en")
    other = add_profile_video(data_dir, "dating", "profile_2", language="en", data=b"other")
    add_title(data_dir / "db" / "db.sqlite3", "dating_profile_2_run_001", "Другой профиль")
    client = _client()

    assert _post(client, language=None, profile="dating/profile_1").status_code == 202
    final = _wait(client, REQUEST_ID_TEXT, "empty", language="en")

    assert final["language"] == "en"
    assert final["files"] == 0
    assert other.is_file()


def test_request_language_rejects_unresolved_request() -> None:
    request = DeliveryRequest(
        profile="dating/profile_1",
        count=1,
        request_id=REQUEST_ID,
    )

    with pytest.raises(DeliveryError, match="язык или профиль"):
        _request_language(request)
