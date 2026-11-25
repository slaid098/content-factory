"""Apply pending database migrations from the command line.

Usage::

    python -m content_factory.db.migrate

The command connects to the configured backend, applies every missing version
and prints the recorded versions. It is idempotent: a second run changes
nothing.
"""

from __future__ import annotations

import sys
from pathlib import Path

from content_factory.config.dsn import redact_database_url
from content_factory.config.settings import get_settings
from content_factory.constants import DB_DIR, DB_FILENAME
from content_factory.db.connection import connect, database_url, sanitize_error_text
from content_factory.db.errors import DatabaseError
from content_factory.db.migrations.runner import applied_versions
from content_factory.exceptions.errors import ConfigError


def main() -> int:
    """Apply migrations and print the ledger; return a process exit code."""
    db_path = _db_path()
    try:
        connection = connect(db_path)
    except (ConfigError, DatabaseError, OSError) as ex:
        sys.stderr.write(f"не удалось подключиться к базе: {sanitize_error_text(str(ex))}\n")
        return 1
    try:
        versions = applied_versions(connection)
    except DatabaseError as ex:
        sys.stderr.write(f"не удалось прочитать версии миграций: {sanitize_error_text(str(ex))}\n")
        return 1
    finally:
        connection.close()
    target = database_url() or str(db_path)
    sys.stdout.write(f"база: {_redact(target)}\n")
    sys.stdout.write(f"применено: {', '.join(versions) if versions else 'нет'}\n")
    return 0


def _redact(target: str) -> str:
    """Hide the credentials of a PostgreSQL DSN before printing it."""
    try:
        return redact_database_url(target)
    except ValueError:
        return target


def _db_path() -> Path:
    """Return the dev SQLite path used when no PostgreSQL DSN is set."""
    return get_settings().data_dir / DB_DIR / DB_FILENAME


if __name__ == "__main__":
    raise SystemExit(main())
