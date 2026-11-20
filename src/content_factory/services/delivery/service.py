"""Content delivery: pick stock, build one archive and clean expired archives."""

from __future__ import annotations

import json
import threading
import zipfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from loguru import logger

from content_factory.constants import ARCHIVE_DIR, CONTENT_DIR, DB_DIR, DB_FILENAME
from content_factory.exceptions.errors import DeliveryError
from content_factory.services.delivery.archives import is_archive_expired
from content_factory.services.delivery.metadata import VideoMeta, load_video_meta
from content_factory.services.delivery.sidecars import read_job_file
from content_factory.services.delivery.titles import load_descriptions, normalize_title

_DELIVERY_LOCK = threading.Lock()
_ZIP_SUFFIX = ".zip"
_TMP_SUFFIX = ".tmp"


def deliver(
    data_dir: Path,
    language: str,
    count: int,
    request_id: UUID,
    query: str | None = None,
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
        query: Exact ``videos.query`` topic; ``None`` selects every topic.
        retention_hours: How long the archive stays idempotent before expiry.
        now: Moment used to test archive freshness; defaults to UTC now.

    Returns:
        Path to the stored archive, or ``None`` when no mp4 matches.

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
            sources = _select_sources(
                data_dir / CONTENT_DIR / language,
                count,
                db_path=data_dir / DB_DIR / DB_FILENAME,
                query=query,
            )
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


def count_archive_files(archive_path: Path) -> int:
    """Return the number of ``NNN.mp4`` entries in a built delivery archive.

    Args:
        archive_path: Path to a zip archive written by :func:`deliver`.

    Returns:
        Number of video entries, one per delivered video.

    Raises:
        OSError: The archive cannot be read.
        zipfile.BadZipFile: The file is not a valid zip archive.
    """
    with zipfile.ZipFile(archive_path) as archive:
        return sum(1 for name in archive.namelist() if name.endswith(".mp4"))


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
    removed = 0
    sidecars_removed = 0
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
            zips, sidecars = _cleanup_directory(directory, moment, retention_hours)
            removed += zips
            sidecars_removed += sidecars
    logger.bind(removed=removed, sidecars=sidecars_removed).info(
        "Очистка архива выдач: удалено {} zip и {} sidecar",
        removed,
        sidecars_removed,
    )
    return removed


def _cleanup_directory(
    directory: Path,
    now: datetime,
    retention_hours: int,
) -> tuple[int, int]:
    """Remove expired zips and sidecars of one language directory."""
    sidecars_removed = _cleanup_sidecars(directory, now)
    return _cleanup_zip_files(directory, now, retention_hours), sidecars_removed


def _cleanup_sidecars(directory: Path, now: datetime) -> int:
    """Remove expired sidecars together with their local zips."""
    try:
        sidecars = list(directory.glob("*.json"))
    except OSError as ex:
        logger.bind(path=str(directory), error=ex, error_type=type(ex).__name__).warning(
            "Не удалось прочитать каталог sidecar {}",
            directory.name,
        )
        return 0
    removed = 0
    for sidecar in sidecars:
        job = read_job_file(sidecar)
        if job is None or job.expires_at > now:
            continue
        try:
            sidecar.unlink()
        except FileNotFoundError:
            continue
        except OSError as ex:
            logger.bind(path=str(sidecar), error=ex, error_type=type(ex).__name__).warning(
                "Не удалось удалить sidecar {}",
                sidecar.name,
            )
            continue
        removed += 1
        _remove_job_zip(directory, job.request_id)
    return removed


def _remove_job_zip(directory: Path, request_id: UUID) -> None:
    """Remove the local zip that belongs to an expired sidecar."""
    try:
        (directory / f"{request_id}{_ZIP_SUFFIX}").unlink(missing_ok=True)
    except OSError as ex:
        logger.bind(error=ex, error_type=type(ex).__name__).warning(
            "Не удалось удалить архив {}",
            request_id,
        )


def _cleanup_zip_files(directory: Path, now: datetime, retention_hours: int) -> int:
    """Remove expired zips without a live sidecar."""
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
            stored_at = datetime.fromtimestamp(archive.stat().st_mtime, tz=UTC)
            if not is_archive_expired(stored_at, retention_hours, now=now):
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
    return not is_archive_expired(stored_at, retention_hours, now=now)


def _remove_stale(path: Path) -> None:
    """Delete an expired archive, tolerating its absence."""
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _select_sources(
    directory: Path,
    count: int,
    *,
    db_path: Path,
    query: str | None = None,
) -> list[Path]:
    """Pick up to ``count`` mp4 files in stable name order, optionally by topic."""
    if not directory.is_dir():
        return []
    candidates = sorted(
        (path for path in directory.iterdir() if path.is_file() and path.suffix == ".mp4"),
        key=lambda path: path.name,
    )
    if query is None:
        return candidates[:count]
    meta = load_video_meta(db_path, [path.stem for path in candidates])
    return [path for path in candidates if _matches_query(meta, path.stem, query)][:count]


def _matches_query(meta: Mapping[str, VideoMeta], sha256: str, query: str) -> bool:
    """Return whether a stock file belongs to the requested topic."""
    item = meta.get(sha256)
    return item is not None and item.query == query


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
