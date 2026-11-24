"""One title assignment pass over the stock."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from content_factory.config.settings import TitlesSettings
from content_factory.constants import CONTENT_DIR, DB_DIR, DB_FILENAME
from content_factory.db.connection import connect
from content_factory.db.titles import ensure_schema, load_titles, recent_titles, save_title
from content_factory.db.video_meta import load_video_meta
from content_factory.exceptions.titles import TitleError
from content_factory.schemas.content.titles import TitlePassResult
from content_factory.schemas.content.video import VideoMeta
from content_factory.services.stock.layout import stock_files
from content_factory.services.titles.client import generate_titles
from content_factory.services.titles.frames import extract_frames
from content_factory.services.titles.text import (
    clean_title,
    description_title_key,
    normalize_title_key,
    validate_title,
)

if TYPE_CHECKING:
    import sqlite3

_RECENT_LIMIT = 40
_EMPTY_META = VideoMeta(description="", query="")


def assign_titles(
    data_dir: Path,
    settings: TitlesSettings,
    *,
    now: datetime | None = None,
) -> TitlePassResult:
    """Generate titles for stock files that have none yet.

    Both layouts are read: the nested ``content/<niche>/<language>`` and the
    legacy flat ``content/<language>``. The language comes from the language
    directory name. A file without a row in ``downloads``/``videos`` is still
    titled from its frames, with an empty description and no duration. Errors
    of one file are logged and skipped; database errors are not swallowed.

    Args:
        data_dir: Root of the runtime data directory.
        settings: Title API settings.
        now: Creation moment of the stored rows; defaults to UTC now.

    Returns:
        Counts of candidates, saved titles and skipped files.
    """
    moment = now or datetime.now(UTC)
    db_path = data_dir / DB_DIR / DB_FILENAME
    files = stock_files(data_dir / CONTENT_DIR)
    connection = connect(db_path)
    try:
        ensure_schema(connection)
        stored = load_titles(db_path, [item.path.stem for item in files])
        pending = [item for item in files if item.path.stem not in stored]
        meta = load_video_meta(db_path, [item.path.stem for item in pending])
        recent = list(recent_titles(db_path))
        titled = 0
        skipped = 0
        for item in pending:
            result, recent = _title_one(
                connection,
                item.path,
                item.language,
                meta.get(item.path.stem, _EMPTY_META),
                recent,
                settings,
                moment,
            )
            if result is None:
                skipped += 1
            else:
                titled += 1
    finally:
        connection.close()
    return TitlePassResult(candidates=len(pending), titled=titled, skipped=skipped)


def _title_one(
    connection: sqlite3.Connection,
    path: Path,
    language: str,
    meta: VideoMeta,
    recent: list[str],
    settings: TitlesSettings,
    moment: datetime,
) -> tuple[str | None, list[str]]:
    """Title one stock file, returning the saved title and the new recent list."""
    try:
        frames = extract_frames(path, meta.duration)
        if not frames:
            logger.bind(path=str(path)).warning("Не удалось получить ни одного кадра")
            return None, recent
        options = generate_titles(
            frames,
            language,
            meta.description,
            meta.query,
            recent,
            settings,
        )
    except (TitleError, OSError) as ex:
        logger.bind(path=str(path), error=ex, error_type=type(ex).__name__).warning(
            "Не удалось подготовить название"
        )
        return None, recent
    saved = _save_candidate(
        connection,
        path.stem,
        options,
        language,
        moment,
        description_title_key(meta.description),
    )
    if saved is None:
        logger.bind(path=str(path)).warning("Все варианты названия заняты или невалидны")
        return None, recent
    return saved, ([saved, *recent])[:_RECENT_LIMIT]


def _save_candidate(
    connection: sqlite3.Connection,
    sha256: str,
    options: tuple[str, ...],
    language: str,
    moment: datetime,
    description_key: str,
) -> str | None:
    """Store the first free and valid option, or return ``None``."""
    for option in options:
        title = clean_title(option)
        if not validate_title(title, language):
            continue
        try:
            norm = normalize_title_key(title)
        except ValueError as ex:
            logger.bind(sha256=sha256, error_type=type(ex).__name__).debug(
                "Вариант названия без букв и цифр пропущен: {}",
                sha256[:8],
            )
            continue
        if description_key and norm == description_key:
            continue
        if save_title(connection, sha256, title, norm, moment.isoformat()):
            return title
    return None
