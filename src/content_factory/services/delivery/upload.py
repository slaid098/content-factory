"""Publish a delivery archive to Filebin temporary file storage."""

from __future__ import annotations

from pathlib import Path

import httpx

from content_factory.exceptions.errors import ArchiveTooLargeError, UploadError

FILEBIN_URL = "https://filebin.net"
_FILEBIN_BIN_PREFIX = "cf-"
MAX_ARCHIVE_BYTES = 1024**3
_UPLOAD_TIMEOUT_SECONDS = 300.0


def upload_archive(
    archive: Path,
    *,
    timeout: float = _UPLOAD_TIMEOUT_SECONDS,
    client: httpx.Client | None = None,
) -> str:
    """Upload one zip archive to Filebin and return its public URL.

    The archive is named after its request id, so the bin and the file name
    are deterministic: ``cf-<request_id>/<request_id>.zip``. A repeated upload
    of the same job lands in the same bin instead of creating another one.

    The URL is never written to the log: it is a public bearer-like link that
    must not end up in service logs or error responses.

    Args:
        archive: Built delivery archive to upload.
        timeout: HTTP timeout in seconds.
        client: Optional HTTP client, used by tests to avoid real network.

    Returns:
        The public URL built from the deterministic bin and file name.

    Raises:
        ArchiveTooLargeError: The archive exceeds the 1 GiB upload limit.
        UploadError: The file cannot be read, Filebin is unreachable,
            answers with a status other than 201, or confirms another file
            name.
    """
    try:
        size = archive.stat().st_size
    except OSError as ex:
        raise UploadError("не удалось прочитать архив для заливки") from ex
    if size > MAX_ARCHIVE_BYTES:
        raise ArchiveTooLargeError("архив больше 1 ГБ, заливка пропущена")
    target = _public_url(archive)
    headers = {
        "Content-Type": "application/zip",
        "Content-Length": str(size),
    }
    try:
        with archive.open("rb") as handle:
            if client is None:
                response = httpx.post(
                    target,
                    content=handle,
                    headers=headers,
                    timeout=timeout,
                )
            else:
                response = client.post(
                    target,
                    content=handle,
                    headers=headers,
                    timeout=timeout,
                )
    except (OSError, httpx.HTTPError) as ex:
        raise UploadError("Filebin недоступен или не ответил вовремя") from ex
    if response.status_code != 201:
        raise UploadError(f"Filebin вернул ошибку (код {response.status_code})")
    _check_file_name(response, archive.name)
    return target


def _public_url(archive: Path) -> str:
    """Build the deterministic public URL of one archive on Filebin."""
    bin_id = f"{_FILEBIN_BIN_PREFIX}{archive.stem}"
    return f"{FILEBIN_URL}/{bin_id}/{archive.name}"


def _check_file_name(response: httpx.Response, filename: str) -> None:
    """Reject a 201 whose JSON names another file; an absent name is fine."""
    try:
        payload = response.json()
    except ValueError:
        return
    if not isinstance(payload, dict):
        return
    file_info = payload.get("file")
    if not isinstance(file_info, dict):
        return
    declared = file_info.get("filename")
    if isinstance(declared, str) and declared != filename:
        raise UploadError("Filebin подтвердил другое имя файла")
