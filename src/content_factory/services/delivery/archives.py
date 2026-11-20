"""Delivery archives: shared TTL, listing and lookup."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from loguru import logger

from content_factory.constants import ARCHIVE_DIR
from content_factory.schemas.content.archives import ArchiveInfo

_ZIP_SUFFIX = ".zip"
_LANGUAGE = re.compile(r"[a-z]{2}")


def archive_expires_at(created_at: datetime, retention_hours: int) -> datetime:
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
    return archive_expires_at(created_at, retention_hours) <= now


def list_archives(
    data_dir: Path,
    retention_hours: int,
    *,
    now: datetime | None = None,
) -> tuple[ArchiveInfo, ...]:
    """List live zip archives of every language, newest first.

    Expired archives are hidden even when cleanup has not removed them yet;
    files with a non-UUID name or a non-zip suffix are ignored.

    Args:
        data_dir: Root of the runtime data directory.
        retention_hours: Archive lifetime in hours.
        now: Moment used to filter expired archives; defaults to UTC now.

    Returns:
        Live archives sorted by creation time in descending order.
    """
    moment = now or datetime.now(UTC)
    archive_root = data_dir / ARCHIVE_DIR
    try:
        language_dirs = sorted(
            path
            for path in archive_root.iterdir()
            if path.is_dir() and _LANGUAGE.fullmatch(path.name)
        )
    except FileNotFoundError:
        return ()
    except OSError as ex:
        logger.bind(
            path=str(archive_root),
            error=ex,
            error_type=type(ex).__name__,
        ).warning("Не удалось прочитать каталог архивов")
        return ()
    archives: list[ArchiveInfo] = []
    for directory in language_dirs:
        archives.extend(_read_directory(directory, retention_hours, moment))
    archives.sort(key=lambda item: item.created_at, reverse=True)
    return tuple(archives)


def find_archive(
    data_dir: Path,
    language: str,
    request_id: UUID,
    retention_hours: int,
    *,
    now: datetime | None = None,
) -> Path | None:
    """Return a live archive path, or ``None`` when it is missing or expired.

    Args:
        data_dir: Root of the runtime data directory.
        language: Validated two-letter language code.
        request_id: Client request identifier.
        retention_hours: Archive lifetime in hours.
        now: Moment used to check the archive age; defaults to UTC now.

    Returns:
        Path to the archive when it can be served.
    """
    if not _LANGUAGE.fullmatch(language):
        return None
    path = data_dir / ARCHIVE_DIR / language / f"{request_id}{_ZIP_SUFFIX}"
    moment = now or datetime.now(UTC)
    try:
        if not path.is_file():
            return None
        stored_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except FileNotFoundError:
        return None
    except OSError as ex:
        logger.bind(path=str(path), error=ex, error_type=type(ex).__name__).warning(
            "Не удалось прочитать архив {}",
            path.name,
        )
        return None
    if is_archive_expired(stored_at, retention_hours, now=moment):
        return None
    return path


def _read_directory(
    directory: Path,
    retention_hours: int,
    now: datetime,
) -> list[ArchiveInfo]:
    """Read live archives of one language directory."""
    try:
        paths = sorted(directory.glob(f"*{_ZIP_SUFFIX}"))
    except OSError as ex:
        logger.bind(path=str(directory), error=ex, error_type=type(ex).__name__).warning(
            "Не удалось прочитать каталог архива {}",
            directory.name,
        )
        return []
    archives: list[ArchiveInfo] = []
    for path in paths:
        info = _read_archive(path, directory.name, retention_hours, now)
        if info is not None:
            archives.append(info)
    return archives


def _read_archive(
    path: Path,
    language: str,
    retention_hours: int,
    now: datetime,
) -> ArchiveInfo | None:
    """Build one archive entry, skipping missing, invalid or expired files."""
    try:
        request_id = UUID(path.stem)
    except ValueError:
        return None
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
        expires_at=archive_expires_at(created_at, retention_hours),
    )
