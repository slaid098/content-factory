"""Background delivery jobs that publish archives to Filebin."""

from __future__ import annotations

import threading
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from loguru import logger

from content_factory.constants import ARCHIVE_DIR
from content_factory.exceptions.errors import (
    ArchiveTooLargeError,
    DeliveryBusyError,
    DeliveryError,
    UploadError,
)
from content_factory.schemas.content.delivery import (
    DeliveryJobInfo,
    DeliveryRequest,
    DeliveryStatus,
)
from content_factory.services.delivery.service import count_archive_files
from content_factory.services.delivery.service import deliver as _deliver
from content_factory.services.delivery.sidecars import (
    delete_job,
    is_job_expired,
    iter_jobs,
    load_job,
    save_job,
)

from content_factory.services.delivery.upload import (  # isort: skip  # один импорт модуля
    MAX_ARCHIVE_BYTES,
    upload_archive as _upload_archive,
)

_JOBS_LOCK = threading.Lock()
_active_request_id: UUID | None = None
_INTERRUPTED_ERROR = "сборка прервана перезапуском API"


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
    """Build one archive through the delivery service capsule."""
    return _deliver(
        data_dir,
        language,
        count,
        request_id,
        query,
        retention_hours=retention_hours,
        now=now,
    )


def upload_archive(archive: Path) -> str:
    """Upload one archive through the Filebin upload capsule."""
    return _upload_archive(archive)


def start_delivery_job(
    data_dir: Path,
    request: DeliveryRequest,
    *,
    retention_hours: int,
    now: datetime | None = None,
) -> DeliveryJobInfo:
    """Start one background build and upload, or return an existing job.

    Only one build may run in the API process. A repeated ``request_id``
    returns its current status without touching the stock; any other id gets
    ``DeliveryBusyError`` while a build is running.

    Args:
        data_dir: Root of the runtime data directory.
        request: Validated delivery request.
        retention_hours: Link and sidecar lifetime in hours.
        now: Moment used to create the sidecar; defaults to UTC now.

    Returns:
        The newly created or already existing job state.

    Raises:
        DeliveryBusyError: Another request is being built right now.
        DeliveryError: The sidecar cannot be written or the worker cannot start.
    """
    moment = now or datetime.now(UTC)
    with _JOBS_LOCK:
        job = _resume_or_conflict(data_dir, request, moment, retention_hours)
        if job is not None:
            return job
        job = _job_state(
            request,
            moment,
            retention_hours,
            status="building",
        )
        _save_or_raise(data_dir, job)
        _set_active(request.request_id)
    thread = threading.Thread(
        target=_run_delivery_job,
        args=(data_dir, request, moment, retention_hours),
        name=f"delivery-{request.request_id}",
        daemon=True,
    )
    try:
        thread.start()
    except RuntimeError as ex:
        _record_failure(data_dir, request, moment, retention_hours)
        _clear_active(request.request_id)
        raise DeliveryError("не удалось запустить сборку выдачи") from ex
    return job


def get_delivery_job(
    data_dir: Path,
    language: str,
    request_id: UUID,
    *,
    retention_hours: int | None = None,
    now: datetime | None = None,
) -> DeliveryJobInfo | None:
    """Return one live job sidecar, deleting it after its TTL."""
    moment = now or datetime.now(UTC)
    job = load_job(data_dir, language, request_id)
    if job is None:
        return None
    expired = is_job_expired(job, now=moment)
    if retention_hours is not None:
        expired = expired or job.created_at + timedelta(hours=retention_hours) <= moment
    if expired:
        _discard_job(data_dir, job)
        return None
    return job


def mark_interrupted_jobs(data_dir: Path) -> int:
    """Mark sidecars left in ``building`` by a previous process as failed."""
    interrupted = 0
    for job in iter_jobs(data_dir):
        if job.status != "building":
            continue
        failed = job.model_copy(
            update={
                "status": "failed",
                "size": 0,
                "files": 0,
                "url": None,
                "error": _INTERRUPTED_ERROR,
            }
        )
        try:
            save_job(data_dir, failed)
        except OSError as ex:
            logger.bind(
                request_id=str(job.request_id),
                error=ex,
                error_type=type(ex).__name__,
            ).warning("Не удалось пометить зависшую выдачу {}", job.request_id)
            continue
        interrupted += 1
    if interrupted:
        logger.bind(count=interrupted).warning(
            "Помечено зависших выдач: {}",
            interrupted,
        )
    return interrupted


def _resume_or_conflict(
    data_dir: Path,
    request: DeliveryRequest,
    moment: datetime,
    retention_hours: int,
) -> DeliveryJobInfo | None:
    """Return a reusable job, recover a stale one, or raise on a busy API."""
    existing = load_job(data_dir, request.language, request.request_id)
    if existing is not None:
        if is_job_expired(existing, now=moment):
            _discard_job(data_dir, existing)
            existing = None
        elif existing.status == "building" and _active_request_id == request.request_id:
            return existing
        elif existing.status == "building":
            existing = _fail_stale(data_dir, existing)
        if existing is not None and existing.status != "failed":
            return existing
    if _active_request_id is not None:
        raise DeliveryBusyError("другая сборка уже выполняется")
    return None


def _fail_stale(data_dir: Path, job: DeliveryJobInfo) -> DeliveryJobInfo:
    """Mark a building sidecar without a worker as failed."""
    failed = job.model_copy(
        update={
            "status": "failed",
            "size": 0,
            "files": 0,
            "url": None,
            "error": _INTERRUPTED_ERROR,
        }
    )
    _save_or_raise(data_dir, failed)
    return failed


def _run_delivery_job(
    data_dir: Path,
    request: DeliveryRequest,
    created_at: datetime,
    retention_hours: int,
) -> None:
    """Build, publish and always release the single-build slot."""
    try:
        job = _build_and_publish(data_dir, request, created_at, retention_hours)
    except Exception as ex:  # a worker must never stay "building"
        logger.bind(
            request_id=str(request.request_id),
            error_type=type(ex).__name__,
        ).error("Неожиданная ошибка фоновой выдачи {}", request.request_id)
        job = _job_state(
            request,
            created_at,
            retention_hours,
            status="failed",
            error="не удалось выполнить выдачу",
        )
    try:
        save_job(data_dir, job)
    except OSError as ex:
        logger.bind(
            request_id=str(request.request_id),
            error=ex,
            error_type=type(ex).__name__,
        ).error("Не удалось сохранить состояние выдачи {}", request.request_id)
    finally:
        _clear_active(request.request_id)


def _build_and_publish(
    data_dir: Path,
    request: DeliveryRequest,
    created_at: datetime,
    retention_hours: int,
) -> DeliveryJobInfo:
    """Run the archive build and upload, mapping every outcome to a status."""
    try:
        archive = _build_archive(data_dir, request, created_at, retention_hours)
    except DeliveryError as ex:
        logger.bind(
            request_id=str(request.request_id),
            error=ex,
            error_type=type(ex).__name__,
        ).error("Не удалось собрать выдачу {}", request.request_id)
        return _job_state(
            request,
            created_at,
            retention_hours,
            status="failed",
            error="не удалось собрать архив",
        )
    if archive is None:
        return _job_state(request, created_at, retention_hours, status="empty")
    try:
        size, files = _read_archive_metrics(archive)
    except (OSError, zipfile.BadZipFile) as ex:
        logger.bind(
            request_id=str(request.request_id),
            error=ex,
            error_type=type(ex).__name__,
        ).error("Не удалось прочитать собранный архив {}", request.request_id)
        return _job_state(
            request,
            created_at,
            retention_hours,
            status="failed",
            error="не удалось прочитать собранный архив",
        )
    if size > MAX_ARCHIVE_BYTES:
        return _ready_local(
            request,
            created_at,
            retention_hours,
            size,
            files,
            "архив больше 1 ГБ, заливка пропущена",
        )
    return _publish_or_fallback(request, created_at, retention_hours, archive, size, files)


def _build_archive(
    data_dir: Path,
    request: DeliveryRequest,
    created_at: datetime,
    retention_hours: int,
) -> Path | None:
    """Build one archive for the request through the delivery capsule."""
    return deliver(
        data_dir,
        request.language,
        request.count,
        request.request_id,
        query=request.query,
        retention_hours=retention_hours,
        now=created_at,
    )


def _read_archive_metrics(archive: Path) -> tuple[int, int]:
    """Return the archive size in bytes and its delivered file count."""
    return archive.stat().st_size, count_archive_files(archive)


def _publish_or_fallback(
    request: DeliveryRequest,
    created_at: datetime,
    retention_hours: int,
    archive: Path,
    size: int,
    files: int,
) -> DeliveryJobInfo:
    """Upload the archive, or keep the zip for a local-only fallback."""
    try:
        url = upload_archive(archive)
    except (ArchiveTooLargeError, UploadError) as ex:
        return _ready_local(request, created_at, retention_hours, size, files, str(ex))
    except Exception as ex:  # keep the zip for a manual retry
        logger.bind(
            request_id=str(request.request_id),
            error_type=type(ex).__name__,
        ).warning("Не удалось залить выдачу {}", request.request_id)
        return _ready_local(
            request,
            created_at,
            retention_hours,
            size,
            files,
            "не удалось загрузить архив на файлообменник",
        )
    return _published_state(request, created_at, retention_hours, archive, size, files, url)


def _published_state(
    request: DeliveryRequest,
    created_at: datetime,
    retention_hours: int,
    archive: Path,
    size: int,
    files: int,
    url: str,
) -> DeliveryJobInfo:
    """Remove the uploaded zip and report the ready job."""
    _remove_archive(archive, request.request_id)
    return _job_state(
        request,
        created_at,
        retention_hours,
        status="ready",
        size=size,
        files=files,
        url=url,
    )


def _ready_local(
    request: DeliveryRequest,
    created_at: datetime,
    retention_hours: int,
    size: int,
    files: int,
    error: str,
) -> DeliveryJobInfo:
    """Keep the built zip and report a local-only fallback."""
    logger.bind(
        request_id=str(request.request_id),
        size=size,
        files=files,
    ).warning("Выдача {} осталась локальной: {}", request.request_id, error)
    return _job_state(
        request,
        created_at,
        retention_hours,
        status="ready_local",
        size=size,
        files=files,
        error=error,
    )


def _job_state(
    request: DeliveryRequest,
    created_at: datetime,
    retention_hours: int,
    *,
    status: DeliveryStatus,
    size: int = 0,
    files: int = 0,
    url: str | None = None,
    error: str | None = None,
) -> DeliveryJobInfo:
    """Build a sidecar value with a TTL counted from the start moment."""
    return DeliveryJobInfo(
        request_id=request.request_id,
        language=request.language,
        status=status,
        created_at=created_at,
        expires_at=created_at + timedelta(hours=retention_hours),
        size=size,
        files=files,
        url=url,
        error=error,
    )


def _save_or_raise(data_dir: Path, job: DeliveryJobInfo) -> None:
    """Persist a sidecar, wrapping filesystem errors as a delivery failure."""
    try:
        save_job(data_dir, job)
    except OSError as ex:
        raise DeliveryError("не удалось сохранить состояние выдачи") from ex


def _remove_archive(archive: Path, request_id: UUID) -> None:
    """Delete the local zip after a successful upload; failure is not fatal."""
    try:
        archive.unlink()
    except OSError as ex:
        logger.bind(
            request_id=str(request_id),
            error=ex,
            error_type=type(ex).__name__,
        ).warning("Не удалось удалить залитый архив {}", request_id)


def _record_failure(
    data_dir: Path,
    request: DeliveryRequest,
    created_at: datetime,
    retention_hours: int,
) -> None:
    """Write a failed sidecar when the worker cannot be started."""
    try:
        save_job(
            data_dir,
            _job_state(
                request,
                created_at,
                retention_hours,
                status="failed",
                error="не удалось запустить сборку выдачи",
            ),
        )
    except OSError as ex:
        logger.bind(
            request_id=str(request.request_id),
            error=ex,
            error_type=type(ex).__name__,
        ).error("Не удалось сохранить состояние выдачи {}", request.request_id)


def _discard_job(data_dir: Path, job: DeliveryJobInfo) -> None:
    """Remove an expired sidecar and its local zip, tolerating races."""
    delete_job(data_dir, job.language, job.request_id)
    path = data_dir / ARCHIVE_DIR / job.language / f"{job.request_id}.zip"
    try:
        path.unlink(missing_ok=True)
    except OSError as ex:
        logger.bind(error=ex, error_type=type(ex).__name__).warning(
            "Не удалось удалить просроченный архив {}",
            job.request_id,
        )


def _set_active(request_id: UUID) -> None:
    """Remember the only build allowed to run."""
    global _active_request_id
    _active_request_id = request_id


def _clear_active(request_id: UUID) -> None:
    """Release the build slot when the worker finishes."""
    global _active_request_id
    with _JOBS_LOCK:
        if _active_request_id == request_id:
            _active_request_id = None
