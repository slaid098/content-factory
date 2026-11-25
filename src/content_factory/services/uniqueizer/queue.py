"""Refill the ready-copy buffer of one profile from its source queue.

The queue knows only the source order, the buffer size and the engine port.
It never inspects the file the engine writes beyond existence and a non-zero
size; everything inside the copy belongs to the engine.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from content_factory.constants import DB_DIR, DB_FILENAME
from content_factory.db.connection import connect
from content_factory.db.sources import (
    get_cursor,
    is_source_path,
    list_next_sources,
    mark_missing,
    mark_used,
    set_cursor,
)
from content_factory.schemas.database.port import Connection
from content_factory.schemas.generator.uniqueizer import (
    RefillFailure,
    RefillResult,
    SourceItem,
)
from content_factory.services.uniqueizer.engine import UniqueizerEngine
from content_factory.services.uniqueizer.layout import (
    buffer_directory,
    copy_name,
    source_directory,
)


class UniqueizerQueue:
    """Buffer refill of one profile; it keeps no state between calls."""

    def refill(
        self,
        language: str,
        profile_id: str,
        target: int,
        data_dir: Path,
        engine: UniqueizerEngine,
    ) -> RefillResult:
        """Create ready copies until the buffer reaches *target*.

        Each source contributes one copy per pass. The cursor advances only
        after the engine has produced a non-empty file; a failure leaves the
        cursor on the last success and removes a partial destination.

        Args:
            language: Validated language code of the queue.
            profile_id: Profile identifier such as ``dating/profile_1``.
            target: Desired number of ready files in the buffer, at least one.
            data_dir: Root of the runtime data directory, usually ``app_data``.
            engine: External uniqueizer that writes one copy.

        Returns:
            Counters of the pass and the last failures.

        Raises:
            ValueError: ``target`` is not positive or the scope is unsafe.
            DatabaseError: The queue cannot be read or written.
        """
        if target <= 0:
            raise ValueError("target должен быть больше нуля")
        buffer = buffer_directory(data_dir, language, profile_id)
        buffer.mkdir(parents=True, exist_ok=True)
        data_root = data_dir.resolve()
        source_root = source_directory(data_dir, language, profile_id).resolve()
        ready = _count_ready(buffer)
        created = 0
        exhausted = False
        failures: list[RefillFailure] = []
        connection = connect(data_dir / DB_DIR / DB_FILENAME)
        try:
            cursor = get_cursor(connection, language, profile_id)
            while ready < target:
                source = _next_source(connection, language, profile_id, cursor)
                if source is None:
                    exhausted = True
                    break
                source_path = _source_file(data_root, source_root, source.path)
                if source_path is None:
                    _mark_unsafe(connection, source)
                    continue
                if not source_path.is_file():
                    _mark_vanished(connection, source)
                    continue
                destination = buffer / copy_name(source_path.name, source.use_count + 1)
                reason = _write_copy(engine, source_path, destination)
                if reason is not None:
                    failures.append(RefillFailure(source=source.path, reason=reason))
                    break
                now = _now()
                mark_used(connection, source.path, now)
                set_cursor(connection, language, profile_id, source.path, now)
                connection.commit()
                cursor = source.path
                created += 1
                ready += 1
        finally:
            connection.close()
        return RefillResult(
            created=created,
            ready=ready,
            exhausted=exhausted,
            failures=tuple(failures),
        )


def _source_file(data_root: Path, source_root: Path, stored: str) -> Path | None:
    """Return a stored source inside the profile folder, or ``None``.

    The stored path is untrusted: it can come from an older or hand-edited
    database. Absolute paths, ``..`` segments and symlinked components are
    rejected before the file can reach the engine.
    """
    if not is_source_path(stored):
        return None
    candidate = data_root / stored
    if not candidate.is_relative_to(source_root) or not candidate.is_relative_to(data_root):
        return None
    current = candidate
    while current != data_root:
        if current.is_symlink():
            return None
        current = current.parent
    return candidate


def _mark_unsafe(connection: Connection, source: SourceItem) -> None:
    """Exclude a source whose stored path leaves its profile folder."""
    mark_missing(connection, [source.path], _now())
    connection.commit()
    logger.bind(path=source.path).warning(
        "Небезопасный путь источника {} исключён из очереди", source.path
    )


def _next_source(
    connection: Connection,
    language: str,
    profile_id: str,
    cursor: str | None,
) -> SourceItem | None:
    """Return the first source after the cursor, or ``None`` at the end."""
    rows = list_next_sources(connection, language, profile_id, cursor, limit=1)
    return rows[0] if rows else None


def _write_copy(engine: UniqueizerEngine, source: Path, destination: Path) -> str | None:
    """Run the engine and validate its destination; return a failure reason."""
    if destination.exists():
        return f"копия уже существует: {destination.name}"
    try:
        engine.uniqueize(source, destination)
    except Exception as ex:
        _remove_partial(destination)
        logger.bind(
            source=str(source),
            destination=str(destination),
            error=ex,
            error_type=type(ex).__name__,
        ).warning("Движок не создал копию источника {}: {}", source.name, ex)
        return f"движок не создал копию: {ex}"
    try:
        if not destination.is_file():
            _remove_partial(destination)
            return f"движок не создал файл: {destination.name}"
        size = destination.stat().st_size
    except OSError:
        _remove_partial(destination)
        return f"движок не создал файл: {destination.name}"
    if size <= 0:
        _remove_partial(destination)
        return f"движок создал пустой файл: {destination.name}"
    return None


def _remove_partial(destination: Path) -> None:
    """Remove a partial copy and keep a cleanup error from hiding the cause."""
    try:
        destination.unlink(missing_ok=True)
    except OSError as ex:
        logger.bind(
            path=str(destination),
            error=ex,
            error_type=type(ex).__name__,
        ).warning("Не удалось удалить частичную копию {}", destination)


def _mark_vanished(connection: Connection, source: SourceItem) -> None:
    """Exclude a source whose file disappeared after the import."""
    mark_missing(connection, [source.path], _now())
    connection.commit()
    logger.bind(path=source.path).warning(
        "Источник {} пропал с диска и исключён из очереди", source.path
    )


def _count_ready(buffer: Path) -> int:
    """Return the number of regular files already in the buffer."""
    try:
        return sum(1 for entry in buffer.iterdir() if entry.is_file())
    except OSError as ex:
        logger.bind(path=str(buffer), error=ex, error_type=type(ex).__name__).warning(
            "Не удалось прочитать буфер {}: {}", buffer, ex
        )
        return 0


def _now() -> str:
    """Return the current moment as ISO 8601 UTC."""
    return datetime.now(UTC).isoformat()


__all__ = ["UniqueizerQueue"]
