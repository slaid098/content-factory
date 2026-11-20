from __future__ import annotations

from pathlib import Path
from uuid import UUID

import httpx
import pytest

from content_factory.exceptions import ArchiveTooLargeError, UploadError
from content_factory.services.delivery import upload

_REQUEST_ID = UUID("11111111-1111-4111-8111-111111111111")


def _archive(tmp_path: Path, data: bytes = b"zip-data") -> Path:
    path = tmp_path / f"{_REQUEST_ID}.zip"
    path.write_bytes(data)
    return path


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler)


def _expected_url() -> str:
    return f"{upload.FILEBIN_URL}/cf-{_REQUEST_ID}/{_REQUEST_ID}.zip"


def test_upload_archive_posts_filebin_contract(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["content_type"] = request.headers["Content-Type"]
        seen["content_length"] = request.headers["Content-Length"]
        seen["body"] = request.read()
        return httpx.Response(
            201,
            json={
                "bin": {"id": f"cf-{_REQUEST_ID}"},
                "file": {"filename": f"{_REQUEST_ID}.zip"},
            },
        )

    with _client(httpx.MockTransport(handler)) as client:
        url = upload.upload_archive(archive, client=client)

    assert url == _expected_url()
    assert seen["method"] == "POST"
    assert seen["url"] == _expected_url()
    assert seen["content_type"] == "application/zip"
    assert seen["content_length"] == str(len(b"zip-data"))
    assert seen["body"] == b"zip-data"


def test_upload_archive_accepts_201_with_invalid_json(tmp_path: Path) -> None:
    archive = _archive(tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, text="created")

    with _client(httpx.MockTransport(handler)) as client:
        url = upload.upload_archive(archive, client=client)

    assert url == _expected_url()


@pytest.mark.parametrize(
    "payload",
    [
        ["not", "an", "object"],
        {"bin": {"id": "cf"}},
        {"file": "not an object"},
        {"file": {"filename": 42}},
    ],
)
def test_upload_archive_accepts_201_without_file_name(
    tmp_path: Path,
    payload: object,
) -> None:
    archive = _archive(tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json=payload)

    with _client(httpx.MockTransport(handler)) as client:
        url = upload.upload_archive(archive, client=client)

    assert url == _expected_url()


def test_upload_archive_rejects_mismatched_file_name(tmp_path: Path) -> None:
    archive = _archive(tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"file": {"filename": "other.zip"}})

    with (
        _client(httpx.MockTransport(handler)) as client,
        pytest.raises(UploadError, match="другое имя файла"),
    ):
        upload.upload_archive(archive, client=client)


def test_upload_archive_rejects_archive_above_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _archive(tmp_path, b"12345")
    monkeypatch.setattr(upload, "MAX_ARCHIVE_BYTES", 4)
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(201)

    with (
        _client(httpx.MockTransport(handler)) as client,
        pytest.raises(ArchiveTooLargeError, match="1 ГБ"),
    ):
        upload.upload_archive(archive, client=client)

    assert called is False


@pytest.mark.parametrize("status", [400, 403, 412, 413, 429, 500, 503])
def test_upload_archive_rejects_non_201_status(tmp_path: Path, status: int) -> None:
    archive = _archive(tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="error")

    with (
        _client(httpx.MockTransport(handler)) as client,
        pytest.raises(UploadError, match=f"код {status}"),
    ):
        upload.upload_archive(archive, client=client)


def test_upload_archive_wraps_connection_error(tmp_path: Path) -> None:
    archive = _archive(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with (
        _client(httpx.MockTransport(handler)) as client,
        pytest.raises(UploadError, match="недоступен"),
    ):
        upload.upload_archive(archive, client=client)


def test_upload_archive_wraps_timeout(tmp_path: Path) -> None:
    archive = _archive(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with (
        _client(httpx.MockTransport(handler)) as client,
        pytest.raises(UploadError, match="не ответил вовремя"),
    ):
        upload.upload_archive(archive, client=client)


def test_upload_archive_wraps_missing_file(tmp_path: Path) -> None:
    with pytest.raises(UploadError, match="не удалось прочитать"):
        upload.upload_archive(tmp_path / f"{_REQUEST_ID}.zip")


def test_upload_archive_uses_default_client_and_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _archive(tmp_path)
    seen: dict[str, object] = {}

    def fake_post(
        url: str,
        *,
        content: object,
        headers: dict[str, str],
        timeout: float,
    ) -> httpx.Response:
        seen["url"] = url
        seen["content_type"] = headers["Content-Type"]
        seen["timeout"] = timeout
        assert hasattr(content, "read")
        seen["body"] = content.read()
        return httpx.Response(201, text="created")

    monkeypatch.setattr(upload.httpx, "post", fake_post)

    assert upload.upload_archive(archive) == _expected_url()
    assert seen["url"] == _expected_url()
    assert seen["content_type"] == "application/zip"
    assert seen["body"] == b"zip-data"
    assert seen["timeout"] == 300.0
