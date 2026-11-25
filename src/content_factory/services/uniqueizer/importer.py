"""Import source videos of a profile into the uniqueizer queue.

The importer reads ``<data_dir>/sources/<language>/<profile_slug>``, probes
every file, hashes it and writes the metadata to ``source_items``. Files that
are not videos, disappeared files and returned files are handled here; the
queue only sees rows whose file is present. A repeated pass is idempotent.

Run the bulk importer with
``python -m content_factory.services.uniqueizer.main --data-dir app_data``.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from content_factory.constants import DB_DIR, DB_FILENAME, LANGUAGE_CODE_RE, SOURCES_DIR
from content_factory.db.connection import connect
from content_factory.db.sources import list_sources, mark_missing, upsert_source
from content_factory.schemas.database.port import Connection
from content_factory.schemas.generator.uniqueizer import (
    ImportResult,
    SourceCandidate,
    SourceImportAction,
)
from content_factory.services.generator import probe
from content_factory.services.profiles import list_profiles
from content_factory.services.uniqueizer.layout import source_directory
from content_factory.utils.filesystem import child_directories

_CHUNK_SIZE = 1024 * 1024


def import_profile(data_dir: Path, language: str, profile_id: str) -> ImportResult:
    """Scan one source folder and merge it into the queue.

    A missing folder yields zero counters. Files without a video stream, with
    a hash error or unreadable metadata are skipped with a warning. A file
    already present with the same hash keeps its usage history; changed
    content resets it; a file whose row was marked missing returns.

    Args:
        data_dir: Root of the runtime data directory, usually ``app_data``.
        language: Validated language code of the folder.
        profile_id: Profile identifier such as ``dating/profile_1``.

    Returns:
        Counters of the pass.

    Raises:
        ValueError: The language or the profile id has an invalid shape.
        DatabaseError: The queue cannot be written.
    """
    directory = source_directory(data_dir, language, profile_id)
    if not directory.is_dir():
        return ImportResult(language=language, profile_id=profile_id)
    counters: Counter[SourceImportAction] = Counter()
    skipped = 0
    seen: set[str] = set()
    connection = connect(data_dir / DB_DIR / DB_FILENAME)
    try:
        now = _now()
        for path in _files(directory):
            candidate = _candidate(data_dir, language, profile_id, path)
            if candidate is None:
                skipped += 1
                continue
            seen.add(candidate.path)
            counters[upsert_source(connection, candidate, now)] += 1
        missing = _mark_missing(connection, language, profile_id, seen, now)
        connection.commit()
    finally:
        connection.close()
    return ImportResult(
        language=language,
        profile_id=profile_id,
        added=counters[SourceImportAction.ADDED],
        updated=counters[SourceImportAction.UPDATED],
        unchanged=counters[SourceImportAction.UNCHANGED],
        returned=counters[SourceImportAction.RETURNED],
        duplicates=counters[SourceImportAction.DUPLICATE],
        missing=missing,
        skipped=skipped,
    )


def import_sources(data_dir: Path) -> tuple[ImportResult, ...]:
    """Import every source folder that belongs to a registered profile.

    Folder names are profile slugs such as ``dating_profile_1``. The registry
    maps a slug back to its profile id; an unknown or ambiguous slug is
    skipped with a warning, because the queue needs the real id.

    Args:
        data_dir: Root of the runtime data directory, usually ``app_data``.

    Returns:
        One result per imported profile in language and slug order.
    """
    root = data_dir / SOURCES_DIR
    if not root.is_dir():
        return ()
    profiles = _profile_ids(data_dir)
    results: list[ImportResult] = []
    for language_dir in child_directories(root):
        if LANGUAGE_CODE_RE.fullmatch(language_dir.name) is None:
            logger.bind(path=str(language_dir)).warning(
                "Каталог {} не похож на язык и пропущен", language_dir
            )
            continue
        for slug_dir in child_directories(language_dir):
            profile_id = profiles.get(slug_dir.name)
            if profile_id is None:
                logger.bind(path=str(slug_dir)).warning(
                    "Каталог источников {} не соответствует одному профилю и пропущен",
                    slug_dir,
                )
                continue
            results.append(import_profile(data_dir, language_dir.name, profile_id))
    return tuple(results)


def _files(directory: Path) -> list[Path]:
    """Return regular files of the folder in name order."""
    try:
        entries = sorted(directory.iterdir(), key=lambda entry: entry.name)
    except OSError as ex:
        logger.bind(path=str(directory), error=ex, error_type=type(ex).__name__).warning(
            "Не удалось прочитать каталог источников {}: {}", directory, ex
        )
        return []
    return [entry for entry in entries if entry.is_file()]


def _candidate(
    data_dir: Path,
    language: str,
    profile_id: str,
    path: Path,
) -> SourceCandidate | None:
    """Probe and hash one file; ``None`` means it must be skipped."""
    info = probe(path)
    if info is None:
        logger.bind(path=str(path)).warning(
            "Файл источника {} пропущен: нет видео-потока или файл не читается", path.name
        )
        return None
    try:
        digest = _sha256(path)
        size = path.stat().st_size
    except OSError as ex:
        logger.bind(path=str(path), error=ex, error_type=type(ex).__name__).warning(
            "Файл источника {} пропущен: не удалось прочитать: {}", path.name, ex
        )
        return None
    return SourceCandidate(
        path=path.relative_to(data_dir).as_posix(),
        language=language,
        profile_id=profile_id,
        sha256=digest,
        size=size,
        duration=info.duration,
    )


def _sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mark_missing(
    connection: Connection,
    language: str,
    profile_id: str,
    seen: set[str],
    now: str,
) -> int:
    """Mark rows of the profile whose files were not in the scan."""
    missing = [
        item.path
        for item in list_sources(connection, language, profile_id)
        if item.path not in seen
    ]
    if not missing:
        return 0
    marked = mark_missing(connection, missing, now)
    logger.bind(language=language, profile_id=profile_id, count=marked).warning(
        "Исходники профиля {} пропали с диска: {}", profile_id, marked
    )
    return marked


def _profile_ids(data_dir: Path) -> dict[str, str]:
    """Map each unambiguous profile slug to its profile id."""
    groups: dict[str, list[str]] = {}
    for profile in list_profiles(data_dir):
        groups.setdefault(profile.id.replace("/", "_"), []).append(profile.id)
    mapping: dict[str, str] = {}
    for slug, ids in sorted(groups.items()):
        if len(ids) == 1:
            mapping[slug] = ids[0]
        else:
            logger.bind(slug=slug, profiles=ids).warning(
                "Слаг {} принадлежит нескольким профилям и пропущен", slug
            )
    return mapping


def _now() -> str:
    """Return the current moment as ISO 8601 UTC."""
    return datetime.now(UTC).isoformat()
