"""Delivery archives: shared TTL, listing and lookup."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import UUID

from loguru import logger

from content_factory.constants import ARCHIVE_DIR
from content_factory.schemas.content.archives import ArchiveInfo
from content_factory.schemas.content.delivery import DeliveryJobInfo
from content_factory.services.delivery.sidecars import (
    delete_job,
    is_job_expired,
    iter_jobs,
    language_directories,
    load_job,
)

_ZIP_SUFFIX = ".zip"
_LANGUAGE = re.compile(r"[a-z]{2}")


@dataclass(frozen=True, slots=True)
class _ArchiveLocation:
    """Where one live archive can be fetched from."""

    storage: Literal["local", "remote"]
    path: Path | None
    url: str | None


def _archive_expires_at(created_at: datetime, retention_hours: int) -> datetime:
    """Return the moment an archive stops being served.

    Args:
        created_at: Archive modification time.
        retention_hours: Archive lifetime in hours.

    Returns:
        The expiry moment in the same timezone as ``created_at``.
    """
    return created_at + timedelta(hours=retention_hours)


def is_archive_expired(
    created_at: datetime,
    retention_hours: int,
    *,
    now: datetime,
) -> bool:
    """Return whether an archive has reached its TTL.

    Args:
        created_at: Archive modification time.
        retention_hours: Archive lifetime in hours.
        now: Moment used to check the archive age.

    Returns:
        True when the archive must not be served anymore.
    """
    return _archive_expires_at(created_at, retention_hours) <= now


def list_archives(
    data_dir: Path,
    retention_hours: int,
    *,
    now: datetime | None = None,
) -> tuple[ArchiveInfo, ...]:
    """List live local and remote archives of every language, newest first.

    A sidecar takes precedence over a legacy zip with the same request id.
    Expired entries are hidden even when cleanup has not removed them yet;
    files with a non-UUID name or a non-zip suffix are ignored.

    Args:
        data_dir: Root of the runtime data directory.
        retention_hours: Archive lifetime in hours.
        now: Moment used to filter expired archives; defaults to UTC now.

    Returns:
        Live archives sorted by creation time in descending order.
    """
    moment = now or datetime.now(UTC)
    jobs = iter_jobs(data_dir)
    sidecar_keys = {(job.language, job.request_id) for job in jobs}
    archives: list[ArchiveInfo] = []
    for job in jobs:
        info = _job_archive(data_dir, job, moment)
        if info is not None:
            archives.append(info)
    for directory in language_directories(data_dir / ARCHIVE_DIR):
        for path in _zip_paths(directory):
            try:
                request_id = UUID(path.stem)
            except ValueError:
                logger.bind(path=str(path), language=directory.name).debug(
                    "Пропущен архив с не-UUID именем {}",
                    path.name,
                )
                continue
            if (directory.name, request_id) in sidecar_keys:
                continue
            info = _read_archive(path, request_id, directory.name, retention_hours, moment)
            if info is not None:
                archives.append(info)
    archives.sort(key=lambda item: item.created_at, reverse=True)
    return tuple(archives)


def resolve_archive(
    data_dir: Path,
    language: str,
    request_id: UUID,
    retention_hours: int,
    *,
    now: datetime | None = None,
) -> _ArchiveLocation | None:
    """Return a live local path or remote URL of one archive.

    A ready sidecar with a URL is always remote, even when the local zip was
    not deleted. A ``ready_local`` sidecar without its zip is gone (``404``).
    """
    if not _LANGUAGE.fullmatch(language):
        return None
    moment = now or datetime.now(UTC)
    job = load_job(data_dir, language, request_id)
    if job is not None:
        if is_job_expired(job, now=moment):
            _discard_expired_job(data_dir, job)
            return None
        if job.status == "ready" and job.url:
            return _ArchiveLocation(storage="remote", path=None, url=job.url)
        if job.status == "ready_local":
            path = _zip_path(data_dir, language, request_id)
            try:
                if path.is_file():
                    return _ArchiveLocation(storage="local", path=path, url=None)
            except OSError as ex:
                logger.bind(error=ex, error_type=type(ex).__name__).warning(
                    "Не удалось прочитать архив {}",
                    path.name,
                )
        return None
    path = _zip_path(data_dir, language, request_id)
    stored_at = _local_mtime(path)
    if stored_at is None:
        return None
    if is_archive_expired(stored_at, retention_hours, now=moment):
        return None
    return _ArchiveLocation(storage="local", path=path, url=None)


def find_archive(
    data_dir: Path,
    language: str,
    request_id: UUID,
    retention_hours: int,
    *,
    now: datetime | None = None,
) -> Path | None:
    """Return a live local archive path, or ``None`` for a remote/missing one.

    Args:
        data_dir: Root of the runtime data directory.
        language: Validated two-letter language code.
        request_id: Client request identifier.
        retention_hours: Archive lifetime in hours.
        now: Moment used to check the archive age; defaults to UTC now.

    Returns:
        Path to the archive when it can be served locally.
    """
    location = resolve_archive(
        data_dir,
        language,
        request_id,
        retention_hours,
        now=now,
    )
    return location.path if location is not None else None


def _job_archive(
    data_dir: Path,
    job: DeliveryJobInfo,
    now: datetime,
) -> ArchiveInfo | None:
    """Build a list entry for a live sidecar with a servable archive."""
    if is_job_expired(job, now=now):
        return None
    if job.status == "ready" and job.url:
        return ArchiveInfo(
            language=job.language,
            request_id=job.request_id,
            size=job.size,
            created_at=job.created_at,
            expires_at=job.expires_at,
            storage="remote",
            url=job.url,
            files=job.files,
        )
    if job.status == "ready_local":
        path = _zip_path(data_dir, job.language, job.request_id)
        try:
            if not path.is_file():
                return None
        except OSError as ex:
            logger.bind(error=ex, error_type=type(ex).__name__).warning(
                "Не удалось прочитать архив {}",
                path.name,
            )
            return None
        return ArchiveInfo(
            language=job.language,
            request_id=job.request_id,
            size=job.size,
            created_at=job.created_at,
            expires_at=job.expires_at,
            storage="local",
            files=job.files,
        )
    return None


def _read_archive(
    path: Path,
    request_id: UUID,
    language: str,
    retention_hours: int,
    now: datetime,
) -> ArchiveInfo | None:
    """Build one archive entry, skipping missing or expired files."""
    try:
        if not path.is_file():
            return None
        stat = path.stat()
    except FileNotFoundError:
        return None
    except OSError as ex:
        logger.bind(path=str(path), error=ex, error_type=type(ex).__name__).warning(
            "Не удалось прочитать архив {}",
            path.name,
        )
        return None
    created_at = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
    if is_archive_expired(created_at, retention_hours, now=now):
        return None
    return ArchiveInfo(
        language=language,
        request_id=request_id,
        size=stat.st_size,
        created_at=created_at,
        expires_at=_archive_expires_at(created_at, retention_hours),
        storage="local",
    )


def _zip_paths(directory: Path) -> list[Path]:
    """Return zip files of one language directory, or none on error."""
    try:
        return sorted(directory.glob(f"*{_ZIP_SUFFIX}"))
    except OSError as ex:
        logger.bind(path=str(directory), error=ex, error_type=type(ex).__name__).warning(
            "Не удалось прочитать каталог архива {}",
            directory.name,
        )
        return []


def _zip_path(data_dir: Path, language: str, request_id: UUID) -> Path:
    """Build the local zip path from validated pieces only."""
    return data_dir / ARCHIVE_DIR / language / f"{request_id}{_ZIP_SUFFIX}"


def _local_mtime(path: Path) -> datetime | None:
    """Return the local zip modification time, or ``None`` when unavailable."""
    try:
        if not path.is_file():
            return None
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except FileNotFoundError:
        return None
    except OSError as ex:
        logger.bind(path=str(path), error=ex, error_type=type(ex).__name__).warning(
            "Не удалось прочитать архив {}",
            path.name,
        )
        return None


def _discard_expired_job(data_dir: Path, job: DeliveryJobInfo) -> None:
    """Remove an expired sidecar and its local zip, tolerating races."""
    delete_job(data_dir, job.language, job.request_id)
    path = _zip_path(data_dir, job.language, job.request_id)
    try:
        path.unlink(missing_ok=True)
    except OSError as ex:
        logger.bind(error=ex, error_type=type(ex).__name__).warning(
            "Не удалось удалить просроченный архив {}",
            job.request_id,
        )
