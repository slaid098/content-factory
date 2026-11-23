"""SQLite access for operational state.

The module is the single place where the service opens SQLite: the API and
the Prefect worker are separate processes sharing ``app_data``, so every
connection needs the same busy timeout, WAL mode and row factory.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

_TIMEOUT_SECONDS = 30
_BUSY_TIMEOUT_MS = 30_000


def connect(db_path: Path) -> sqlite3.Connection:
    """Open the state database, creating its directory and enabling WAL.

    Args:
        db_path: Path to the operational SQLite database.

    Returns:
        A writable connection with ``busy_timeout`` and foreign keys enabled.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=_TIMEOUT_SECONDS)
    try:
        _configure(connection)
        connection.execute("PRAGMA journal_mode=WAL")
    except BaseException:
        connection.close()
        raise
    return connection


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    """Open an existing state database without creating or changing it.

    Args:
        db_path: Path to an existing operational SQLite database.

    Returns:
        A read-only connection with the same timeouts as :func:`connect`.
    """
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=_TIMEOUT_SECONDS)
    _configure(connection)
    return connection


def _configure(connection: sqlite3.Connection) -> None:
    """Apply the pragmas shared by writable and read-only connections."""
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA foreign_keys=ON")
