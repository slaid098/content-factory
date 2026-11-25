"""Import sources of every registered profile into the uniqueizer queue.

Usage::

    python -m content_factory.services.uniqueizer.main --data-dir app_data

The command scans ``<data_dir>/sources/<language>/<profile_slug>`` folders of
profiles known to the registry, imports every source and prints counters of
the pass per profile. A repeated run is idempotent.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from content_factory.config.settings import get_settings
from content_factory.db.errors import DatabaseError
from content_factory.exceptions.errors import ConfigError
from content_factory.schemas.generator.uniqueizer import ImportResult
from content_factory.services.uniqueizer.importer import import_sources


def main(argv: Sequence[str] | None = None) -> int:
    """Run the bulk importer and print counters per profile."""
    parser = argparse.ArgumentParser(
        prog="python -m content_factory.services.uniqueizer.main",
        description="Импорт источников уникализатора",
    )
    parser.add_argument("--data-dir", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        data_dir = args.data_dir or get_settings().data_dir
        results = import_sources(data_dir)
    except (ConfigError, DatabaseError, OSError) as ex:
        sys.stderr.write(f"не удалось импортировать источники: {ex}\n")
        return 1
    if not results:
        sys.stdout.write("источники не найдены\n")
    for result in results:
        sys.stdout.write(f"{_line(result)}\n")
    return 0


def _line(result: ImportResult) -> str:
    """Format the counters of one profile for the command line."""
    return (
        f"{result.language} {result.profile_id}: "
        f"добавлено {result.added}, обновлено {result.updated}, "
        f"без изменений {result.unchanged}, вернулось {result.returned}, "
        f"дубликатов {result.duplicates}, конфликтов {result.conflicts}, "
        f"пропущено {result.skipped}, пропало {result.missing}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
