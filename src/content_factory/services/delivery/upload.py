"""Publish a delivery archive to Litterbox temporary file storage."""

from __future__ import annotations

from pathlib import Path
from typing import IO
from urllib.parse import urlsplit

import httpx

from content_factory.exceptions.errors import ArchiveTooLargeError, UploadError

LITTERBOX_URL = "https://litterbox.catbox.moe/resources/internals/api.php"
LITTERBOX_TIME = "24h"
MAX_ARCHIVE_BYTES = 1024**3
MAX_UPLOAD_BYTES = MAX_ARCHIVE_BYTES
UPLOAD_TIMEOUT_SECONDS = 120.0


def upload_archive(
    archive: Path,
    *,
    timeout: float = UPLOAD_TIMEOUT_SECONDS,
    client: httpx.Client | None = None,
) -> str:
    """Upload one zip archive and return its public temporary URL.

    The URL is never written to the log: it is a public bearer-like link that
    must not end up in service logs or error responses.

    Args:
        archive: Built delivery archive to upload.
        timeout: HTTP timeout in seconds.
        client: Optional HTTP client, used by tests to avoid real network.

    Returns:
        The absolute public URL returned by Litterbox.

    Raises:
        ArchiveTooLargeError: The archive exceeds the 1 GiB upload limit.
        UploadError: The file cannot be read, the service is unreachable or
            the response is not a valid URL.
    """
    try:
        size = archive.stat().st_size
    except OSError as ex:
        raise UploadError("не удалось прочитать архив для заливки") from ex
    if size > MAX_ARCHIVE_BYTES:
        raise ArchiveTooLargeError("архив больше 1 ГБ, заливка пропущена")
    try:
        with archive.open("rb") as handle:
            files: dict[str, tuple[str, IO[bytes], str]] = {
                "fileToUpload": (archive.name, handle, "application/zip")
            }
            data = {"reqtype": "fileupload", "time": LITTERBOX_TIME}
            if client is None:
                response = httpx.post(
                    LITTERBOX_URL,
                    data=data,
                    files=files,
                    timeout=timeout,
                )
            else:
                response = client.post(
                    LITTERBOX_URL,
                    data=data,
                    files=files,
                    timeout=timeout,
                )
    except (OSError, httpx.HTTPError) as ex:
        raise UploadError("Litterbox недоступен или не ответил вовремя") from ex
    if not 200 <= response.status_code < 300:
        raise UploadError(f"Litterbox вернул ошибку (код {response.status_code})")
    url = response.text.strip()
    if not _is_public_url(url):
        raise UploadError("Litterbox вернул некорректный URL")
    return url


def _is_public_url(value: str) -> bool:
    """Return whether *value* is a single absolute HTTP(S) URL."""
    if not value or any(char.isspace() for char in value):
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme in {"http", "https"} and bool(parts.netloc)
