"""Copy the operational state of a SQLite database into the configured one.

Usage::

    python -m content_factory.db.import_sqlite --sqlite app_data/db/db.sqlite3

The source is opened read-only; the destination is the database selected by
``CONTENT_FACTORY_DATABASE_URL`` (PostgreSQL) or the default SQLite file. The
copy covers the seven operational tables and runs in one transaction, so a
failure leaves the destination untouched. Every insert uses
``ON CONFLICT DO NOTHING``, which makes a repeated import harmless and keeps
the delivery history of rows that already exist.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from content_factory.config.settings import get_settings
from content_factory.constants import DB_DIR, DB_FILENAME
from content_factory.db.connection import Connection, connect
from content_factory.db.errors import DatabaseError

# Parent tables come before their dependants: downloads references videos.
TABLES = (
    "stock_items",
    "titles",
    "profile_settings",
    "runtime_state",
    "alert_cooldowns",
    "videos",
    "downloads",
)

_BATCH_SIZE = 500


def import_sqlite(sqlite_path: Path) -> dict[str, int]:
    """Copy the operational tables of one SQLite file into the configured DB.

    Args:
        sqlite_path: Existing SQLite database to read.

    Returns:
        Number of rows inserted per table.

    Raises:
        OSError: The source file cannot be opened.
        DatabaseError: The destination rejects the copy; it is rolled back.
        ValueError: The source and the destination are the same SQLite file.
    """
    db_path = get_settings().data_dir / DB_DIR / DB_FILENAME
    if not sqlite_path.is_file():
        raise OSError(f"файл базы не найден: {sqlite_path}")
    source = sqlite3.connect(f"{sqlite_path.resolve().as_uri()}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        missing = [table for table in TABLES if not _has_source_table(source, table)]
        if missing:
            raise ValueError(f"в источнике нет таблиц: {', '.join(missing)}")
        destination = connect(db_path)
        if destination.dialect == "sqlite" and db_path.resolve() == sqlite_path.resolve():
            destination.close()
            raise ValueError("источник и приёмник — один и тот же файл SQLite")
        try:
            counts = _copy(source, destination)
            destination.commit()
        except BaseException:
            destination.rollback()
            raise
        finally:
            destination.close()
    finally:
        source.close()
    return counts


def _copy(source: sqlite3.Connection, destination: Connection) -> dict[str, int]:
    """Insert every row of the known tables, skipping duplicates."""
    counts: dict[str, int] = {}
    for table in TABLES:
        columns = _source_columns(source, table) & destination.columns(table)
        if not columns:
            counts[table] = 0
            continue
        names = ", ".join(sorted(columns))
        placeholders = ", ".join("?" for _ in columns)
        statement = f"INSERT INTO {table} ({names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
        rows = source.execute(
            f"SELECT {names} FROM {table}",
        ).fetchall()
        inserted = 0
        for start in range(0, len(rows), _BATCH_SIZE):
            batch = [tuple(row) for row in rows[start : start + _BATCH_SIZE]]
            cursor = destination.executemany(statement, batch)
            inserted += cursor.rowcount
        counts[table] = inserted
    return counts


def _has_source_table(source: sqlite3.Connection, table: str) -> bool:
    """Return whether the source SQLite file has the table."""
    row = source.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _source_columns(source: sqlite3.Connection, table: str) -> set[str]:
    """Return the column names of one source table."""
    rows = source.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]) for row in rows}


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="python -m content_factory.db.import_sqlite",
        description="Перенести операционное состояние SQLite в текущую базу.",
    )
    parser.add_argument(
        "--sqlite",
        required=True,
        type=Path,
        help="путь к исходной базе SQLite",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the import and return a process exit code."""
    args = _parse_args(argv)
    try:
        counts = import_sqlite(args.sqlite)
    except (OSError, ValueError, DatabaseError, sqlite3.Error) as ex:
        sys.stderr.write(f"импорт не выполнен: {ex}\n")
        return 1
    for table, amount in counts.items():
        sys.stdout.write(f"{table}: {amount}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
