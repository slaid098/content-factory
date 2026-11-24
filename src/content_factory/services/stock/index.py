"""Synchronize the SQLite stock index with the content directory."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from content_factory.constants import CONTENT_DIR, DB_DIR, DB_FILENAME
from content_factory.db.connection import connect
from content_factory.db.stock import ensure_schema, prune_missing, upsert_items
from content_factory.schemas.stock.layout import StockFile
from content_factory.services.stock.layout import stock_files

if TYPE_CHECKING:
    import sqlite3

_ScanRow = tuple[StockFile, int, str]


def sync_stock(data_dir: Path) -> int:
    """Index every stock file on disk and prune undelivered missing rows.

    The scan is one pass over ``content``; paths are stored relative to
    ``data_dir`` in POSIX form. A repeated call is idempotent: delivered rows
    keep their delivery history and no duplicate path appears.

    Args:
        data_dir: Root of the runtime data directory.

    Returns:
        Number of rows in the stock table after synchronization.
    """
    connection = connect(data_dir / DB_DIR / DB_FILENAME)
    try:
        ensure_schema(connection)
        rows = _scan(data_dir)
        upsert_items(connection, rows)
        prune_missing(connection, {item.path.as_posix() for item, _, _ in rows})
        connection.commit()
        return _row_count(connection)
    finally:
        connection.close()


def _scan(data_dir: Path) -> list[_ScanRow]:
    """Read the stock directory and build one upsert row per readable file."""
    rows: list[_ScanRow] = []
    for item in stock_files(data_dir / CONTENT_DIR):
        try:
            stat = item.path.stat()
        except OSError as ex:
            logger.bind(
                path=str(item.path),
                error=ex,
                error_type=type(ex).__name__,
            ).warning("Не удалось прочитать файл стока {} и он пропущен", item.path)
            continue
        rows.append(
            (
                StockFile(
                    niche=item.niche,
                    language=item.language,
                    path=item.path.relative_to(data_dir),
                    profile_id=item.profile_id,
                ),
                stat.st_size,
                _created_at(stat.st_mtime),
            )
        )
    return rows


def _created_at(mtime: float) -> str:
    """Serialize a file modification time as ISO 8601 UTC."""
    return datetime.fromtimestamp(mtime, tz=UTC).isoformat()


def _row_count(connection: sqlite3.Connection) -> int:
    """Return the number of rows in the stock table."""
    row = connection.execute("SELECT COUNT(*) FROM stock_items").fetchone()
    return 0 if row is None else int(row[0])


__all__ = ["sync_stock"]
