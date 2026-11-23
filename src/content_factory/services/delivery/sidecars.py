"""Sidecar files of asynchronous temporary-link delivery jobs."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import UUID

from loguru import logger
from pydantic import ValidationError

from content_factory.constants import ARCHIVE_DIR, LANGUAGE_CODE_RE
from content_factory.schemas.content.delivery import DeliveryJobInfo

SIDECAR_SUFFIX = ".json"
_TMP_SUFFIX = ".tmp"


def is_job_expired(job: DeliveryJobInfo, *, now: datetime) -> bool:
    """Return whether a temporary-link job has reached its stored TTL."""
    return job.expires_at <= now


def load_job(
    data_dir: Path,
    language: str,
    request_id: UUID,
) -> DeliveryJobInfo | None:
    """Read one sidecar, returning ``None`` when it is missing or invalid.

    The path is built only from a validated language and a UUID.
    """
    if not LANGUAGE_CODE_RE.fullmatch(language):
        return None
    return _read_job(job_path(data_dir, language, request_id), language, request_id)


def save_job(data_dir: Path, job: DeliveryJobInfo) -> None:
    """Write one sidecar atomically next to its local archive."""
    path = job_path(data_dir, job.language, job.request_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}{_TMP_SUFFIX}")
    try:
        tmp_path.write_text(job.model_dump_json(), encoding="utf-8")
        tmp_path.replace(path)
    except OSError:
        _discard(tmp_path)
        raise


def delete_job(data_dir: Path, language: str, request_id: UUID) -> None:
    """Delete one sidecar, tolerating its absence and a race."""
    if not LANGUAGE_CODE_RE.fullmatch(language):
        return
    try:
        job_path(data_dir, language, request_id).unlink(missing_ok=True)
    except OSError as ex:
        logger.bind(error=ex, error_type=type(ex).__name__).warning(
            "Не удалось удалить sidecar выдачи {}",
            request_id,
        )


def iter_jobs(data_dir: Path) -> tuple[DeliveryJobInfo, ...]:
    """Read every valid sidecar of every language directory."""
    jobs: list[DeliveryJobInfo] = []
    for directory in language_directories(data_dir / ARCHIVE_DIR):
        try:
            paths = sorted(directory.glob(f"*{SIDECAR_SUFFIX}"))
        except OSError as ex:
            logger.bind(path=str(directory), error=ex, error_type=type(ex).__name__).warning(
                "Не удалось прочитать каталог sidecar {}",
                directory.name,
            )
            continue
        for path in paths:
            job = read_job_file(path)
            if job is not None:
                jobs.append(job)
    return tuple(jobs)


def read_job_file(path: Path) -> DeliveryJobInfo | None:
    """Read one sidecar found while scanning a language directory."""
    try:
        request_id = UUID(path.stem)
    except ValueError:
        return None
    language = path.parent.name
    if not LANGUAGE_CODE_RE.fullmatch(language):
        return None
    return _read_job(path, language, request_id)


def job_path(data_dir: Path, language: str, request_id: UUID) -> Path:
    """Build the sidecar path from validated pieces only."""
    if not LANGUAGE_CODE_RE.fullmatch(language):
        raise ValueError("invalid language")
    return data_dir / ARCHIVE_DIR / language / f"{request_id}{SIDECAR_SUFFIX}"


def language_directories(root: Path) -> list[Path]:
    """Return language directories of the archive root, or none on error."""
    try:
        return sorted(
            path
            for path in root.iterdir()
            if path.is_dir() and LANGUAGE_CODE_RE.fullmatch(path.name)
        )
    except FileNotFoundError:
        return []
    except OSError as ex:
        logger.bind(path=str(root), error=ex, error_type=type(ex).__name__).warning(
            "Не удалось прочитать каталог архивов"
        )
        return []


def _read_job(path: Path, language: str, request_id: UUID) -> DeliveryJobInfo | None:
    """Read and validate one sidecar against its expected identity."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as ex:
        logger.bind(path=str(path), error=ex, error_type=type(ex).__name__).warning(
            "Не удалось прочитать sidecar выдачи {}",
            request_id,
        )
        return None
    try:
        job = DeliveryJobInfo.model_validate_json(text)
    except (ValidationError, ValueError):
        logger.bind(path=str(path)).warning("Некорректный sidecar выдачи {}", request_id)
        return None
    if job.language != language or job.request_id != request_id:
        return None
    return job


def _discard(path: Path) -> None:
    """Remove a staging file after a failed sidecar write."""
    try:
        path.unlink(missing_ok=True)
    except OSError as ex:
        logger.bind(error=ex, error_type=type(ex).__name__).warning(
            "Не удалось удалить временный sidecar"
        )
