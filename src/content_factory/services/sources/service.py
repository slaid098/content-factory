"""Import profile sources into the database and plan the next batch.

The files stay on disk under ``app_data/sources``; the database only keeps
their metadata and the usage cursor. The module hashes and probes every file,
so a broken source never enters the pool, and marks rows whose file vanished.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from content_factory.constants import (
    DB_DIR,
    DB_FILENAME,
    LANGUAGE_CODE_RE,
    PROFILES_DIR,
    SOURCES_DIR,
)
from content_factory.db.connection import connect, db_exists
from content_factory.db.errors import DatabaseError
from content_factory.db.sources import mark_missing, mark_used, select_next, upsert_import
from content_factory.exceptions.generator import GeneratorError
from content_factory.schemas.database.port import Connection
from content_factory.schemas.generator.source import (
    ImportStats,
    ProfileImportStats,
    SourceImportAction,
    SourceItem,
)
from content_factory.services.generator import probe

_HASH_CHUNK = 1024 * 1024


@dataclass(frozen=True, slots=True)
class _Identity:
    """Profile id and language read from a profile directory."""

    profile_id: str
    language: str


def import_sources(data_dir: Path, sources_root: Path | None = None) -> ImportStats:
    """Scan the source tree and synchronize the pool rows.

    Args:
        data_dir: Root ``app_data`` directory with profiles and the database.
        sources_root: Source tree; defaults to ``data_dir/sources``. It must
            stay inside *data_dir*, because stored paths are relative to it.

    Returns:
        Per-profile counters, skipped files and warning messages.

    Raises:
        GeneratorError: The source tree is missing, outside the data
            directory, a file cannot be read, or the rows cannot be written.
        OSError: A source file cannot be read.
    """
    root = _checked_root(data_dir, sources_root)
    if not root.is_dir():
        raise GeneratorError(f"каталог исходников не найден: {root}")
    scan_root = _scan_root(data_dir, root)
    stats = ImportStats()
    connection = connect(data_dir / DB_DIR / DB_FILENAME)
    try:
        _sync_pool(connection, data_dir, root, scan_root, stats)
        connection.commit()
    except DatabaseError as ex:
        connection.rollback()
        raise GeneratorError(f"не удалось записать пул исходников: {ex}") from ex
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    return stats


def _sync_pool(
    connection: Connection,
    data_dir: Path,
    root: Path,
    scan_root: str,
    stats: ImportStats,
) -> None:
    """Upsert scanned files and mark the rows missing from this root."""
    profiles = _profile_index(data_dir)
    now = datetime.now(UTC).isoformat()
    present: set[str] = set()
    for path in sorted(root.rglob("*.mp4")):
        if path.is_file():
            parts = path.relative_to(root).parts
            _import_file(connection, data_dir, path, parts, profiles, stats, present, now)
    for item in mark_missing(connection, present, now, root=scan_root):
        _profile(stats, item.language, item.profile_id).missing += 1


def _import_file(
    connection: Connection,
    data_dir: Path,
    path: Path,
    parts: tuple[str, ...],
    profiles: dict[str, _Identity],
    stats: ImportStats,
    present: set[str],
    now: str,
) -> None:
    """Validate and upsert one file found by the scan."""
    identity = _identity(parts, profiles, path, stats)
    if identity is None:
        return
    try:
        stored_path = path.resolve().relative_to(data_dir.resolve()).as_posix()
    except (OSError, ValueError):
        _skip(stats, path, "файл лежит вне каталога данных")
        return
    present.add(stored_path)
    try:
        digest = _sha256(path)
        info = probe(path)
    except OSError as ex:
        _skip(stats, path, f"файл не прочитан: {ex}")
        return
    if info is None or not info.has_video:
        _skip(stats, path, "нет видео-потока или файл битый")
        return
    action = upsert_import(
        connection,
        language=identity.language,
        profile_id=identity.profile_id,
        path=stored_path,
        sha256=digest,
        size=info.size,
        duration=info.duration,
        now=now,
    )
    _count(stats, identity.language, identity.profile_id, action)


def _identity(
    parts: tuple[str, ...],
    profiles: dict[str, _Identity],
    path: Path,
    stats: ImportStats,
) -> _Identity | None:
    """Read the language and profile of a scanned file, or skip it."""
    if len(parts) < 3:
        _skip(stats, path, "ожидался <language>/<profile_slug>/<name>.mp4")
        return None
    language, slug = parts[0], parts[1]
    if LANGUAGE_CODE_RE.fullmatch(language) is None:
        _skip(stats, path, f"недопустимый код языка {language!r}")
        return None
    identity = profiles.get(slug)
    if identity is None:
        _skip(stats, path, f"профиль {slug!r} не найден в {PROFILES_DIR}")
        return None
    if identity.language and identity.language != language:
        _skip(
            stats,
            path,
            f"язык папки {language!r} не совпадает с языком профиля {identity.language!r}",
        )
        return None
    return identity


def plan_next(
    data_dir: Path,
    language: str,
    profile_id: str,
    count: int,
) -> tuple[SourceItem, ...]:
    """Read the next sources of a profile without changing the cursor.

    Args:
        data_dir: Root ``app_data`` directory.
        language: Language of the profile.
        profile_id: Profile identifier.
        count: Number of sources the batch needs.

    Returns:
        Exactly *count* sources in cursor order.

    Raises:
        GeneratorError: The database or the pool is missing, or the pool has
            fewer than *count* available sources.
        DatabaseError: The database cannot be read.
    """
    db_path = data_dir / DB_DIR / DB_FILENAME
    if not db_exists(db_path):
        raise GeneratorError(
            f"пул исходников профиля {profile_id} пуст: нет базы {db_path}; "
            "сначала импортируйте sources"
        )
    connection = connect(db_path)
    try:
        sources = select_next(connection, language, profile_id, count)
    finally:
        connection.close()
    require_pool(sources, count, profile_id)
    return sources


def mark_published(data_dir: Path, source_id: int) -> None:
    """Record one successful publication and move the profile cursor.

    Args:
        data_dir: Root ``app_data`` directory with the operational database.
        source_id: Primary key of the published source.

    Raises:
        GeneratorError: The pool database or the row is missing, or the
            transaction cannot be written.
    """
    db_path = data_dir / DB_DIR / DB_FILENAME
    if not db_exists(db_path):
        raise GeneratorError(f"пул исходников не найден: нет базы {db_path}")
    connection = connect(db_path)
    try:
        mark_used(connection, source_id, datetime.now(UTC).isoformat())
        connection.commit()
    except DatabaseError as ex:
        connection.rollback()
        raise GeneratorError(f"не удалось отметить исходник id={source_id}: {ex}") from ex
    finally:
        connection.close()


def require_pool(
    sources: tuple[SourceItem, ...],
    count: int,
    profile_id: str,
) -> None:
    """Fail before rendering when the pool cannot fill the batch.

    Args:
        sources: Sources selected for the batch.
        count: Requested number of items.
        profile_id: Profile identifier used in the error message.

    Raises:
        GeneratorError: The pool is empty or shorter than *count*.
    """
    if not sources:
        raise GeneratorError(
            f"пул исходников профиля {profile_id} пуст: импортируйте файлы "
            "командой python -m content_factory.services.sources.main"
        )
    if len(sources) < count:
        raise GeneratorError(
            f"в пуле профиля {profile_id} доступно {len(sources)} исходников, "
            f"нужно {count}: партия не начата"
        )


def _checked_root(data_dir: Path, sources_root: Path | None) -> Path:
    """Resolve the source tree and keep it inside the data directory."""
    data = data_dir.expanduser().resolve()
    root = (sources_root or data / SOURCES_DIR).expanduser().resolve()
    if not root.is_relative_to(data):
        raise GeneratorError(f"каталог исходников должен лежать внутри {data}: {root}")
    return root


def _scan_root(data_dir: Path, root: Path) -> str:
    """Return the scanned root relative to the data directory for mark_missing."""
    relative = root.relative_to(data_dir.expanduser().resolve()).as_posix()
    return "" if relative == "." else relative


def _profile_index(data_dir: Path) -> dict[str, _Identity]:
    """Map ``profile_slug`` to the profile id and its declared language."""
    root = data_dir.expanduser() / PROFILES_DIR
    if not root.is_dir():
        return {}
    index: dict[str, _Identity] = {}
    for path in sorted(root.rglob("profile.yaml")):
        try:
            relative = path.parent.relative_to(root)
        except ValueError:
            logger.bind(path=str(path)).warning("Профиль лежит вне каталога профилей — пропущен")
            continue
        parts = relative.parts
        if len(parts) < 2 or any(part.startswith((".", "_")) for part in parts):
            continue
        profile_id = "/".join(parts)
        slug = profile_id.replace("/", "_")
        if slug in index:
            logger.bind(slug=slug, profile=profile_id).warning(
                "Slug профиля совпал с другим профилем — файлы пропущены"
            )
            continue
        index[slug] = _Identity(profile_id, _read_language(path))
    return index


def _read_language(path: Path) -> str:
    """Read the optional ``language`` field of a profile YAML."""
    try:
        payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return ""
    if not isinstance(payload, dict):
        return ""
    return str(payload.get("language") or "").strip()


def _sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of a file."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _profile(stats: ImportStats, language: str, profile_id: str) -> ProfileImportStats:
    """Return the counters of one profile, creating them on first use."""
    key = f"{language}/{profile_id}"
    return stats.profiles.setdefault(key, ProfileImportStats())


def _count(
    stats: ImportStats,
    language: str,
    profile_id: str,
    action: SourceImportAction,
) -> None:
    """Add one upsert outcome to the profile counters."""
    profile = _profile(stats, language, profile_id)
    if action is SourceImportAction.ADDED:
        profile.added += 1
    elif action is SourceImportAction.UPDATED:
        profile.updated += 1
    elif action is SourceImportAction.RESTORED:
        profile.restored += 1
    elif action is SourceImportAction.UNCHANGED:
        profile.unchanged += 1
    else:
        profile.duplicates += 1


def _skip(stats: ImportStats, path: Path, reason: str) -> None:
    """Count and log one skipped file."""
    stats.skipped += 1
    message = f"{path}: {reason}"
    stats.warnings.append(message)
    logger.bind(path=str(path), reason=reason).warning("Исходник пропущен при импорте")


__all__ = [
    "ImportStats",
    "ProfileImportStats",
    "import_sources",
    "mark_published",
    "plan_next",
    "require_pool",
]
