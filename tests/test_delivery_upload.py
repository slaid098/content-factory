from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from content_factory.exceptions import ArchiveTooLargeError, UploadError
from content_factory.services.delivery import upload


def _archive(tmp_path: Path, data: bytes = b"zip-data") -> Path:
    path = tmp_path / "delivery.zip"
    path.write_bytes(data)
    return path


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(transport=handler)


def test_upload_archive_posts_litterbox_contract(tmp_path: Path) -> None:
    archive = _archive(tmp_path)
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.read()
        return httpx.Response(200, text="https://litter.catbox.moe/example.zip")

    with _client(httpx.MockTransport(handler)) as client:
        url = upload.upload_archive(archive, client=client)

    assert url == "https://litter.catbox.moe/example.zip"
    assert seen["url"] == upload.LITTERBOX_URL
    body = seen["body"]
    assert isinstance(body, bytes)
    assert b'name="reqtype"' in body
    assert b"fileupload" in body
    assert b'name="time"' in body
    assert b"24h" in body
    assert b'name="fileToUpload"' in body
    assert b"zip-data" in body


def test_upload_archive_rejects_archive_above_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = _archive(tmp_path, b"12345")
    monkeypatch.setattr(upload, "MAX_ARCHIVE_BYTES", 4)

    with pytest.raises(ArchiveTooLargeError, match="1 ГБ"):
        upload.upload_archive(archive)


def test_upload_archive_rejects_non_url_response(tmp_path: Path) -> None:
    archive = _archive(tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not a public url")

    with (
        _client(httpx.MockTransport(handler)) as client,
        pytest.raises(UploadError, match="некорректный URL"),
    ):
        upload.upload_archive(archive, client=client)


def test_upload_archive_rejects_http_error_status(tmp_path: Path) -> None:
    archive = _archive(tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="busy")

    with (
        _client(httpx.MockTransport(handler)) as client,
        pytest.raises(UploadError, match="код 503"),
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


def test_upload_archive_wraps_missing_file(tmp_path: Path) -> None:
    with pytest.raises(UploadError, match="не удалось прочитать"):
        upload.upload_archive(tmp_path / "missing.zip")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://litter.catbox.moe/a.zip", True),
        ("http://example.test/a.zip", True),
        ("ftp://example.test/a.zip", False),
        ("litter.catbox.moe/a.zip", False),
        ("https://example.test/a b.zip", False),
        ("", False),
    ],
)
def test_public_url_validation(value: str, expected: bool) -> None:
    assert upload._is_public_url(value) is expected
