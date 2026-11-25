"""Content delivery: pick stock, build one archive and clean expired archives."""

from __future__ import annotations

import zipfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from loguru import logger

from content_factory.constants import ARCHIVE_DIR, DB_DIR, DB_FILENAME
from content_factory.db.connection import connect, connect_readonly
from content_factory.db.delivery import load_language_queries as load_language_queries
from content_factory.db.errors import DatabaseError
from content_factory.db.lock import DELIVERY_LOCK, lock_path, locked
from content_factory.db.stock import list_available, mark_delivered
from content_factory.db.titles import load_titles
from content_factory.db.video_meta import load_video_meta as load_video_meta
from content_factory.exceptions.errors import DeliveryError, LockError
from content_factory.schemas.content.delivery import GENERAL_PROFILE, ContentType
from content_factory.schemas.content.video import VideoMeta
from content_factory.schemas.profiles.delivery import TitleConfig, TitlePolicy
from content_factory.services.delivery.archives import is_archive_expired
from content_factory.services.delivery.packing import (
    build_archive as _build_archive,
)
from content_factory.services.delivery.packing import (
    count_archive_files as count_archive_files,
)
from content_factory.services.delivery.packing import discard_tmp as _discard_tmp
from content_factory.services.delivery.packing import remove_sources as _remove_sources
from content_factory.services.delivery.sidecars import read_job_file
from content_factory.services.profiles import get_profile, title_policy
from content_factory.services.stock import sync_stock

_ZIP_SUFFIX = ".zip"
_GENERAL_NICHE = "general"


def deliver(
    data_dir: Path,
    language: str,
    count: int,
    request_id: UUID,
    query: str | None = None,
    *,
    profile: str | None = None,
    content_type: ContentType = ContentType.VIDEO,
    retention_hours: int,
    now: datetime | None = None,
) -> Path | None:
    """Deliver up to ``count`` stock files of one language as a zip archive.

    The delivered media and photo sidecars are removed only after the archive
    is written. A repeated ``request_id`` within the retention window returns
    the existing archive without touching the stock. The lock serializes
    deliveries so the same file is never handed out twice.

    Args:
        data_dir: Root of the runtime data directory.
        language: Validated two- or three-letter language code of the address.
        count: Maximum number of files to deliver.
        request_id: Client request identifier, also the archive file name.
        query: Exact ``videos.query`` topic; ``None`` selects every topic.
        profile: Profile identifier that narrows the address to its stock rows
            of the same language; ``general`` is the same as no profile.
        content_type: Media type of the archive; photos exist only in a
            profile, because General is video by language.
        retention_hours: How long the archive stays idempotent before expiry.
        now: Moment used to test archive freshness; defaults to UTC now.

    Returns:
        Path to the stored archive, or ``None`` when no matching file exists.

    Raises:
        DeliveryError: The archive directory or zip cannot be written, or the
            profile belongs to another language.
        ProfileError: The profile is unsafe, missing or malformed.
    """
    profile_id = None if profile == GENERAL_PROFILE else profile
    profile_info = None if profile_id is None else get_profile(data_dir, profile_id)
    resolved = _checked_language(
        language,
        None if profile_info is None else profile_info.language,
    )
    policy = title_policy(
        profile_info.delivery.title if profile_info is not None else TitleConfig()
    )
    hashtags = () if profile_info is None else profile_info.delivery.hashtags
    moment = now or datetime.now(UTC)
    try:
        with locked(lock_path(data_dir / DB_DIR, DELIVERY_LOCK)):
            return _deliver_locked(
                data_dir,
                resolved,
                count,
                request_id,
                query,
                profile_id=profile_id,
                content_type=content_type,
                retention_hours=retention_hours,
                now=moment,
                policy=policy,
                hashtags=hashtags,
            )
    except LockError as ex:
        raise DeliveryError(f"не удалось заблокировать выдачу {request_id}") from ex


def _checked_language(language: str, profile_language: str | None) -> str:
    """Return the request language after checking it matches the profile.

    Raises:
        DeliveryError: The profile belongs to another language.
    """
    if profile_language is not None and profile_language != language:
        raise DeliveryError("язык профиля не совпадает с языком запроса")
    return language


def _deliver_locked(
    data_dir: Path,
    language: str,
    count: int,
    request_id: UUID,
    query: str | None,
    *,
    profile_id: str | None,
    content_type: ContentType,
    retention_hours: int,
    now: datetime,
    policy: TitlePolicy,
    hashtags: tuple[str, ...],
) -> Path | None:
    """Build or reuse one archive while holding the delivery lock."""
    archive_dir = data_dir / ARCHIVE_DIR / language
    archive_path = archive_dir / f"{request_id}{_ZIP_SUFFIX}"

    if _is_fresh(archive_path, now, retention_hours):
        logger.bind(request_id=str(request_id), path=str(archive_path)).info(
            "Выдача {} уже собрана", request_id
        )
        return archive_path
    if content_type is ContentType.PHOTO and profile_id is None:
        return None

    try:
        _remove_stale(archive_path)
        sources, titles = _select_sources(
            _available_sources(data_dir, language, profile_id, content_type),
            count,
            db_path=data_dir / DB_DIR / DB_FILENAME,
            content_type=content_type,
            query=query,
            policy=policy,
        )
        if not sources:
            return None
        archive_dir.mkdir(parents=True, exist_ok=True)
        _build_archive(
            archive_path,
            sources,
            titles,
            content_type=content_type,
            language=language,
            profile_id=profile_id,
            hashtags=hashtags,
        )
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as ex:
        _discard_tmp(archive_path)
        raise DeliveryError(f"не удалось собрать архив {request_id}") from ex

    try:
        _mark_delivered(data_dir, sources, request_id, now)
    except (OSError, DatabaseError) as ex:
        raise DeliveryError(f"не удалось отметить выдачу {request_id}") from ex
    _remove_sources(sources, content_type)
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


def _available_sources(
    data_dir: Path,
    language: str,
    profile_id: str | None = None,
    content_type: ContentType = ContentType.VIDEO,
) -> list[Path]:
    """Synchronize the index and return existing undelivered files of a language.

    A database failure is logged and yields no candidates: delivery must not
    hand out stock it cannot record as delivered. With a profile the candidates
    are the rows of that profile id; without one they are the general niche
    rows that have no profile, so the two delivery modes never mix. The type
    narrows the candidates to videos or to photos.

    Args:
        data_dir: Root of the runtime data directory.
        language: Validated two- or three-letter language code.
        profile_id: Restrict candidates to this profile; ``None`` returns the
            general stock without a profile.
        content_type: Media type of the requested rows.

    Returns:
        Absolute paths of files that still exist on disk, in path order.
    """
    try:
        sync_stock(data_dir)
    except (OSError, DatabaseError) as ex:
        logger.bind(
            path=str(data_dir / DB_DIR / DB_FILENAME),
            error=ex,
            error_type=type(ex).__name__,
        ).warning("Не удалось синхронизировать индекс стока")
        return []
    connection = connect_readonly(data_dir / DB_DIR / DB_FILENAME)
    try:
        rows = list_available(
            connection,
            language,
            content_type=content_type,
            profile_id=profile_id,
            niche=_GENERAL_NICHE if profile_id is None else None,
            unassigned_only=profile_id is None,
        )
    finally:
        connection.close()
    sources: list[Path] = []
    for relative, _profile in rows:
        path = data_dir / relative
        if not path.is_file():
            logger.bind(path=relative, language=language).warning(
                "Файл стока исчез после синхронизации и пропущен: {}", relative
            )
            continue
        sources.append(path)
    return sources


def _select_sources(
    candidates: Sequence[Path],
    count: int,
    *,
    db_path: Path,
    policy: TitlePolicy,
    content_type: ContentType = ContentType.VIDEO,
    query: str | None = None,
) -> tuple[list[Path], dict[str, str]]:
    """Pick up to ``count`` candidates and the titles of selected videos.

    The title policy decides which videos are ready: ``generated`` needs a
    non-empty stored title, while ``fixed`` selects every candidate and takes
    the profile title instead of the stored one. A stored title is returned
    unchanged. A photo needs no title, because its text comes from the sidecar.
    """
    if content_type is ContentType.PHOTO:
        return list(candidates[:count]), {}
    stored = load_titles(db_path, [path.stem for path in candidates])
    selected = _policy_pairs(candidates, policy, stored)
    if query is not None:
        meta = load_video_meta(db_path, [path.stem for path, _title in selected])
        selected = [
            (path, title) for path, title in selected if _matches_query(meta, path.stem, query)
        ]
    picked = selected[:count]
    return [path for path, _title in picked], {path.stem: title for path, title in picked}


def _policy_pairs(
    candidates: Sequence[Path],
    policy: TitlePolicy,
    stored: Mapping[str, str],
) -> list[tuple[Path, str]]:
    """Return candidates with a title under the policy, in candidate order."""
    pairs: list[tuple[Path, str]] = []
    for path in candidates:
        title = policy.title_for(path.stem, stored)
        if title is not None:
            pairs.append((path, title))
    return pairs


def _mark_delivered(
    data_dir: Path,
    sources: Sequence[Path],
    request_id: UUID,
    now: datetime,
) -> None:
    """Record the delivered stock rows before their files are removed."""
    paths = [source.relative_to(data_dir).as_posix() for source in sources]
    connection = connect(data_dir / DB_DIR / DB_FILENAME)
    try:
        mark_delivered(connection, paths, str(request_id), now.isoformat())
        connection.commit()
    finally:
        connection.close()


def _matches_query(meta: Mapping[str, VideoMeta], sha256: str, query: str) -> bool:
    """Return whether a stock file belongs to the requested topic."""
    item = meta.get(sha256)
    return item is not None and item.query == query
