"""Stock accounting."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path

from loguru import logger

from content_factory.constants import CONTENT_DIR, DB_DIR, DB_FILENAME
from content_factory.db.connection import connect_readonly
from content_factory.db.runtime_state import read_state
from content_factory.db.titles import load_titles
from content_factory.schemas.config.runtime import LanguageConfig
from content_factory.schemas.state.state import RuntimeState
from content_factory.schemas.stock.stock import LanguageStock, StockSnapshot
from content_factory.services.stock.layout import StockFile, language_files


def count_stock(content_dir: Path, languages: Iterable[str]) -> dict[str, int]:
    """Посчитать готовые mp4-файлы языка по всем нишам стока."""
    return {language: len(language_files(content_dir, language)) for language in languages}


def collect_stock(
    data_dir: Path,
    languages: Mapping[str, LanguageConfig],
    *,
    now: datetime,
) -> tuple[StockSnapshot, RuntimeState]:
    """Собрать остатки включённых языков и сохранённое состояние без записи.

    Args:
        data_dir: Корень ``app_data``.
        languages: Языки из конфигурации.
        now: Момент снятия остатков.

    Returns:
        Снимок остатков и последнее сохранённое состояние цикла.
    """
    enabled = {code: config for code, config in sorted(languages.items()) if config.enabled}
    content_dir = data_dir / CONTENT_DIR
    files = {code: language_files(content_dir, code) for code in enabled}
    hashes = [item.path.stem for items in files.values() for item in items]
    try:
        titles = load_titles(data_dir / DB_DIR / DB_FILENAME, hashes)
    except OSError as ex:
        logger.bind(error=ex, error_type=type(ex).__name__).warning(
            "Не удалось прочитать названия видео"
        )
        titles = {}
    rows = tuple(
        _language_stock(code, enabled[code], paths, titles) for code, paths in files.items()
    )
    snapshot = StockSnapshot(generated_at=now, languages=rows)
    return snapshot, _read_runtime_state(data_dir)


def _language_stock(
    code: str,
    config: LanguageConfig,
    files: Sequence[StockFile],
    titles: Mapping[str, str],
) -> LanguageStock:
    """Build the stock row of one language from its files and stored titles."""
    stock = len(files)
    titled = sum(1 for item in files if titles.get(item.path.stem, "").strip())
    return LanguageStock(
        language=code,
        stock=stock,
        target=config.stock_target,
        deficit=max(0, config.stock_target - stock),
        titled=titled,
        untitled=stock - titled,
        video_bytes=sum(_file_size(item.path) for item in files),
    )


def _file_size(path: Path) -> int:
    """Return the file size, logging an unreadable file as zero bytes."""
    try:
        return path.stat().st_size
    except OSError as ex:
        logger.bind(path=str(path), error=ex, error_type=type(ex).__name__).warning(
            "Не удалось прочитать размер видео {}",
            path,
        )
        return 0


def _read_runtime_state(data_dir: Path) -> RuntimeState:
    """Read the loop state without creating a database file."""
    db_path = data_dir / DB_DIR / DB_FILENAME
    try:
        if not db_path.is_file():
            return RuntimeState.empty()
        connection = connect_readonly(db_path)
        try:
            return read_state(connection)
        finally:
            connection.close()
    except (OSError, sqlite3.Error, ValueError, TypeError) as ex:
        logger.bind(path=str(db_path), error=ex, error_type=type(ex).__name__).warning(
            "Не удалось прочитать состояние цикла"
        )
        return RuntimeState.empty()
