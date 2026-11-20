"""Content delivery: pick stock, build one archive and clean expired archives."""

from __future__ import annotations

import json
import threading
import zipfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from loguru import logger

from content_factory.constants import ARCHIVE_DIR, CONTENT_DIR, DB_DIR, DB_FILENAME
from content_factory.exceptions.errors import DeliveryError
from content_factory.services.delivery.titles import load_descriptions, normalize_title

_DELIVERY_LOCK = threading.Lock()
_ZIP_SUFFIX = ".zip"
_TMP_SUFFIX = ".tmp"
_SECONDS_PER_HOUR = 3600


def deliver(
    data_dir: Path,
    language: str,
    count: int,
    request_id: UUID,
    *,
    retention_hours: int,
    now: datetime | None = None,
) -> Path | None:
    """Deliver up to ``count`` stock videos of one language as a zip archive.

    The delivered ``.mp4`` files are removed only after the archive is written.
    A repeated ``request_id`` within the retention window returns the existing
    archive without touching the stock. The lock serializes deliveries so the
    same file is never handed out twice.

    Args:
        data_dir: Root of the runtime data directory.
        language: Validated two-letter language code.
        count: Maximum number of videos to deliver.
        request_id: Client request identifier, also the archive file name.
        retention_hours: How long the archive stays idempotent before expiry.
        now: Moment used to test archive freshness; defaults to UTC now.

    Returns:
        Path to the stored archive, or ``None`` when the language has no mp4.

    Raises:
        DeliveryError: The archive directory or zip cannot be written.
    """
    moment = now or datetime.now(UTC)
    archive_dir = data_dir / ARCHIVE_DIR / language
    archive_path = archive_dir / f"{request_id}{_ZIP_SUFFIX}"

    with _DELIVERY_LOCK:
        if _is_fresh(archive_path, moment, retention_hours):
            logger.bind(request_id=str(request_id), path=str(archive_path)).info(
                "Выдача {} уже собрана", request_id
            )
            return archive_path

        try:
            _remove_stale(archive_path)
            sources = _select_sources(data_dir / CONTENT_DIR / language, count)
            if not sources:
                return None
            archive_dir.mkdir(parents=True, exist_ok=True)
            descriptions = load_descriptions(
                data_dir / DB_DIR / DB_FILENAME,
                [source.stem for source in sources],
            )
            _build_archive(archive_path, sources, descriptions, language)
        except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as ex:
            _discard_tmp(archive_path)
            raise DeliveryError(f"не удалось собрать архив {request_id}") from ex

        _remove_sources(sources)
        logger.bind(
            request_id=str(request_id),
            language=language,
            files=len(sources),
            path=str(archive_path),
        ).info("Собрана выдача {}: {} файлов", request_id, len(sources))
        return archive_path


def cleanup_expired_archives(
    data_dir: Path,
    retention_hours: int = 24,
    *,
    now: datetime | None = None,
) -> int:
    """Delete delivery archives older than the retention window.

    Cleanup is best effort: an unreadable directory or a race with another
    process is logged and never stops the API. The delivery lock keeps the
    scheduler from deleting an archive while a request is being assembled.

    Args:
        data_dir: Root of the runtime data directory.
        retention_hours: Archive lifetime in hours.
        now: Moment used to check archive age; defaults to UTC now.

    Returns:
        Number of removed zip files.
    """
    moment = now or datetime.now(UTC)
    cutoff = moment.timestamp() - retention_hours * _SECONDS_PER_HOUR
    removed = 0
    with _DELIVERY_LOCK:
        archive_root = data_dir / ARCHIVE_DIR
        try:
            language_dirs = sorted(path for path in archive_root.iterdir() if path.is_dir())
        except FileNotFoundError:
            return 0
        except OSError as ex:
            logger.bind(
                path=str(archive_root),
                error=ex,
                error_type=type(ex).__name__,
            ).warning("Не удалось прочитать каталог архивов")
            return 0
        for directory in language_dirs:
            removed += _cleanup_directory(directory, cutoff)
    logger.bind(removed=removed).info("Очистка архива выдач: удалено {} zip", removed)
    return removed


def _cleanup_directory(directory: Path, cutoff: float) -> int:
    """Remove expired zips of one language directory."""
    try:
        archives = list(directory.glob(f"*{_ZIP_SUFFIX}"))
    except OSError as ex:
        logger.bind(path=str(directory), error=ex, error_type=type(ex).__name__).warning(
            "Не удалось прочитать каталог архива {}",
            directory.name,
        )
        return 0
    removed = 0
    for archive in archives:
        try:
            if archive.stat().st_mtime >= cutoff:
                continue
            archive.unlink()
            removed += 1
        except FileNotFoundError:
            continue
        except OSError as ex:
            logger.bind(path=str(archive), error=ex, error_type=type(ex).__name__).warning(
                "Не удалось удалить архив {}",
                archive.name,
            )
    return removed


def _is_fresh(path: Path, now: datetime, retention_hours: int) -> bool:
    """Return whether an archive exists and is younger than the retention."""
    try:
        stored_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    except FileNotFoundError:
        return False
    return now - stored_at < timedelta(hours=retention_hours)


def _remove_stale(path: Path) -> None:
    """Delete an expired archive, tolerating its absence."""
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _select_sources(directory: Path, count: int) -> list[Path]:
    """Pick up to ``count`` mp4 files in stable name order."""
    if not directory.is_dir():
        return []
    candidates = sorted(
        (path for path in directory.iterdir() if path.is_file() and path.suffix == ".mp4"),
        key=lambda path: path.name,
    )
    return candidates[:count]


def _build_archive(
    archive_path: Path,
    sources: Sequence[Path],
    descriptions: Mapping[str, str],
    language: str,
) -> None:
    """Write the archive next to its final name and atomically replace it."""
    tmp_path = _tmp_path(archive_path)
    tmp_path.unlink(missing_ok=True)
    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, source in enumerate(sources, start=1):
            prefix = f"{index:03d}"
            archive.write(source, arcname=f"{prefix}.mp4")
            title = normalize_title(descriptions.get(source.stem), language)
            archive.writestr(
                f"{prefix}.json",
                json.dumps({"title": title}, ensure_ascii=False),
            )
    tmp_path.replace(archive_path)


def _tmp_path(archive_path: Path) -> Path:
    """Return the staging path of an archive."""
    return archive_path.with_suffix(_TMP_SUFFIX)


def _discard_tmp(archive_path: Path) -> None:
    """Remove the staging file after a failed build, logging a failed unlink."""
    try:
        _tmp_path(archive_path).unlink(missing_ok=True)
    except OSError as ex:
        logger.bind(error=ex, error_type=type(ex).__name__).warning(
            "Не удалось удалить временный архив {}",
            archive_path.name,
        )


def _remove_sources(sources: Sequence[Path]) -> None:
    """Delete delivered source files; a failed unlink does not fail the request."""
    for source in sources:
        try:
            source.unlink()
        except OSError as ex:
            logger.bind(path=str(source), error=ex, error_type=type(ex).__name__).warning(
                "Не удалось удалить выданный файл {}",
                source.name,
            )
