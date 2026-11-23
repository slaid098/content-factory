"""Database reads used by the delivery capsule."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from loguru import logger

from content_factory.db.connection import connect_readonly


def load_language_queries(db_path: Path, language: str) -> list[str] | None:
    """Return distinct database queries of a language, or ``None`` without a DB.

    A missing or unreadable database must not stop the caller: the query list
    is then built only from the language file.

    Args:
        db_path: Path to the operational SQLite database.
        language: Validated two-letter language code.

    Returns:
        Sorted distinct queries, or ``None`` when the database is absent or
        cannot be read.
    """
    if not db_path.is_file():
        return None
    try:
        connection = connect_readonly(db_path)
        try:
            rows = connection.execute(
                "SELECT DISTINCT query FROM videos "
                "WHERE language = ? AND query <> '' ORDER BY query",
                (language,),
            ).fetchall()
        finally:
            connection.close()
    except (OSError, sqlite3.Error) as ex:
        logger.bind(error=ex, error_type=type(ex).__name__).warning(
            "Не удалось прочитать запросы из базы"
        )
        return None
    return [str(row["query"]) for row in rows]
