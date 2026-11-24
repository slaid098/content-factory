"""Content delivery: pick stock, build one archive and clean expired archives."""

from __future__ import annotations

import json
import zipfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from loguru import logger

from content_factory.constants import ARCHIVE_DIR, CONTENT_DIR, DB_DIR, DB_FILENAME
from content_factory.db.delivery import load_language_queries as load_language_queries
from content_factory.db.lock import DELIVERY_LOCK, lock_path, locked
from content_factory.db.titles import load_titles
from content_factory.db.video_meta import load_video_meta as load_video_meta
from content_factory.exceptions.errors import DeliveryError, LockError
from content_factory.schemas.content.video import VideoMeta
from content_factory.services.delivery.archives import is_archive_expired
from content_factory.services.delivery.sidecars import read_job_file
from content_factory.services.stock import language_files

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
        language: Validated two- or three-letter language code.
        count: Maximum number of videos to deliver.
        request_id: Client request identifier, also the archive file name.
        query: Exact ``videos.query`` topic; ``None`` selects every topic.
        retention_hours: How long the archive stays idempotent before expiry.
        now: Moment used to test archive freshness; defaults to UTC now.

    Returns:
        Path to the stored archive, or ``None`` when no titled mp4 matches.

    Raises:
        DeliveryError: The archive directory or zip cannot be written.
    """
    moment = now or datetime.now(UTC)
    try:
        with locked(lock_path(data_dir / DB_DIR, DELIVERY_LOCK)):
            return _deliver_locked(
                data_dir,
                language,
                count,
                request_id,
                query,
                retention_hours=retention_hours,
                now=moment,
            )
    except LockError as ex:
        raise DeliveryError(f"не удалось заблокировать выдачу {request_id}") from ex


def _deliver_locked(
    data_dir: Path,
    language: str,
    count: int,
    request_id: UUID,
    query: str | None,
    *,
    retention_hours: int,
    now: datetime,
) -> Path | None:
    """Build or reuse one archive while holding the delivery lock."""
    archive_dir = data_dir / ARCHIVE_DIR / language
    archive_path = archive_dir / f"{request_id}{_ZIP_SUFFIX}"

    if _is_fresh(archive_path, now, retention_hours):
        logger.bind(request_id=str(request_id), path=str(archive_path)).info(
            "Выдача {} уже собрана", request_id
        )
        return archive_path

    try:
        _remove_stale(archive_path)
        sources, titles = _select_sources(
            data_dir / CONTENT_DIR,
            language,
            count,
            db_path=data_dir / DB_DIR / DB_FILENAME,
            query=query,
        )
        if not sources or not titles:
            return None
        archive_dir.mkdir(parents=True, exist_ok=True)
        _build_archive(archive_path, sources, titles)
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

    Cleanup is best effort: an unreadable directory, a busy lock or a race
    with another process is logged and never stops the API. The cross-process
    delivery lock keeps the scheduler from deleting an archive while a
    request is being assembled.

    Args:
        data_dir: Root of the runtime data directory.
        retention_hours: Archive lifetime in hours.
        now: Moment used to check archive age; defaults to UTC now.

    Returns:
        Number of removed zip files.
    """
    moment = now or datetime.now(UTC)
    try:
        with locked(lock_path(data_dir / DB_DIR, DELIVERY_LOCK)):
            removed, sidecars_removed = _cleanup_archives(data_dir, moment, retention_hours)
    except LockError as ex:
        logger.bind(error=ex, error_type=type(ex).__name__).warning(
            "Не удалось заблокировать очистку архива выдач"
        )
        return 0
    logger.bind(removed=removed, sidecars=sidecars_removed).info(
        "Очистка архива выдач: удалено {} zip и {} sidecar",
        removed,
        sidecars_removed,
    )
    return removed


def _cleanup_archives(
    data_dir: Path,
    now: datetime,
    retention_hours: int,
) -> tuple[int, int]:
    """Remove expired zips and sidecars of every language directory."""
    archive_root = data_dir / ARCHIVE_DIR
    try:
        language_dirs = sorted(path for path in archive_root.iterdir() if path.is_dir())
    except FileNotFoundError:
        return 0, 0
    except OSError as ex:
        logger.bind(
            path=str(archive_root),
            error=ex,
            error_type=type(ex).__name__,
        ).warning("Не удалось прочитать каталог архивов")
        return 0, 0
    removed = 0
    sidecars_removed = 0
    for directory in language_dirs:
        zips, sidecars = _cleanup_directory(directory, now, retention_hours)
        removed += zips
        sidecars_removed += sidecars
    return removed, sidecars_removed


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
            logger.bind(
                path=str(sidecar),
                request_id=str(job.request_id),
            ).debug(
                "Sidecar уже удалён другим процессом {}",
                sidecar.name,
            )
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
            logger.bind(
                path=str(archive),
                request_id=archive.stem,
            ).debug(
                "Архив уже удалён другим процессом {}",
                archive.name,
            )
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
    path.unlink(missing_ok=True)


def _select_sources(
    content_dir: Path,
    language: str,
    count: int,
    *,
    db_path: Path,
    query: str | None = None,
) -> tuple[list[Path], dict[str, str]]:
    """Pick up to ``count`` titled mp4 files of one language and their titles.

    Candidates come from every niche of the language in ``(niche, name)``
    order. A title made only of whitespace counts as absent, so such a file
    is never selected. A stored title is returned unchanged and is not
    trimmed.
    """
    candidates = language_files(content_dir, language)
    stored = load_titles(db_path, [item.path.stem for item in candidates])
    titled = [item for item in candidates if stored.get(item.path.stem, "").strip()]
    if query is not None:
        meta = load_video_meta(db_path, [item.path.stem for item in titled])
        titled = [item for item in titled if _matches_query(meta, item.path.stem, query)]
    selected = titled[:count]
    return [item.path for item in selected], {
        item.path.stem: stored[item.path.stem] for item in selected
    }


def _matches_query(meta: Mapping[str, VideoMeta], sha256: str, query: str) -> bool:
    """Return whether a stock file belongs to the requested topic."""
    item = meta.get(sha256)
    return item is not None and item.query == query


def _build_archive(
    archive_path: Path,
    sources: Sequence[Path],
    titles: Mapping[str, str],
) -> None:
    """Write the archive next to its final name and atomically replace it.

    Any failure removes the staging file before the exception is re-raised, so
    a failed build never leaves ``.tmp`` next to the archive.
    """
    tmp_path = _tmp_path(archive_path)
    replaced = False
    try:
        tmp_path.unlink(missing_ok=True)
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for index, source in enumerate(sources, start=1):
                prefix = f"{index:03d}"
                archive.write(source, arcname=f"{prefix}.mp4")
                archive.writestr(
                    f"{prefix}.json",
                    json.dumps({"title": titles[source.stem]}, ensure_ascii=False),
                )
        tmp_path.replace(archive_path)
        replaced = True
    finally:
        if not replaced:
            _discard_tmp(archive_path)


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
