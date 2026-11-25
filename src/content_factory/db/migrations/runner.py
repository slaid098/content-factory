"""Apply forward-only migrations once under a cross-process lock.

The runner records every version in ``schema_migrations``. PostgreSQL keeps
the lock in a session advisory lock; SQLite reuses the file lock next to the
database, so two processes that start together apply each migration once.
"""

from __future__ import annotations

from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from types import ModuleType

from content_factory.db.lock import lock_path, locked
from content_factory.db.migrations import MIGRATION_MODULES
from content_factory.schemas.database.port import Connection

MIGRATIONS_LOCK = "migrations"
_POSTGRES_ADVISORY_LOCK_KEY = 0x63665F6D69677261

_SCHEMA_MIGRATIONS = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
)
"""


def apply_migrations(connection: Connection, *, db_path: Path) -> tuple[str, ...]:
    """Apply every migration missing from ``schema_migrations``.

    Args:
        connection: Writable connection to the target database.
        db_path: SQLite path; its parent holds the migration lock file. The
            path is unused in PostgreSQL mode.

    Returns:
        Versions applied by this call, in order.

    Raises:
        DatabaseError: A migration fails; its transaction is rolled back.
    """
    if connection.dialect == "postgres":
        return _with_postgres_lock(connection)
    with locked(lock_path(db_path.parent, MIGRATIONS_LOCK)):
        return _apply_pending(connection)


def applied_versions(connection: Connection) -> tuple[str, ...]:
    """Return recorded migration versions in application order."""
    rows = connection.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    return tuple(str(row["version"]) for row in rows)


def _with_postgres_lock(connection: Connection) -> tuple[str, ...]:
    """Hold the session advisory lock while migrations run."""
    connection.execute("SELECT pg_advisory_lock(?)", (_POSTGRES_ADVISORY_LOCK_KEY,))
    connection.commit()
    try:
        return _apply_pending(connection)
    finally:
        connection.execute("SELECT pg_advisory_unlock(?)", (_POSTGRES_ADVISORY_LOCK_KEY,))
        connection.commit()


def _apply_pending(connection: Connection) -> tuple[str, ...]:
    """Create the ledger and apply missing versions one transaction each."""
    connection.execute(_SCHEMA_MIGRATIONS)
    connection.commit()
    known = set(applied_versions(connection))
    applied: list[str] = []
    for migration in _migrations():
        version = str(migration.VERSION)
        if version in known:
            continue
        try:
            migration.apply(connection)
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(UTC).isoformat()),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        applied.append(version)
    return tuple(applied)


def _migrations() -> tuple[ModuleType, ...]:
    """Import migration modules in their declared order."""
    return tuple(import_module(name) for name in MIGRATION_MODULES)


__all__ = ["MIGRATIONS_LOCK", "applied_versions", "apply_migrations"]
