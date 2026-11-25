"""Command line of the profile source import.

Usage::

    python -m content_factory.services.sources.main --sources app_data/sources

Without ``--sources`` the importer scans ``<data_dir>/sources``. The command
is idempotent and prints the counters of every language and profile.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from content_factory.config.settings import get_settings
from content_factory.exceptions.generator import GeneratorError
from content_factory.schemas.generator.source import ImportStats
from content_factory.services.sources.service import import_sources


def main(argv: list[str] | None = None) -> int:
    """Import the source tree and return a process exit code."""
    args = _parse_args(argv)
    data_dir = get_settings().data_dir
    sources = None if args.sources is None else _absolute(args.sources)
    try:
        stats = import_sources(data_dir, sources)
    except (GeneratorError, OSError) as ex:
        sys.stderr.write(f"импорт исходников не выполнен: {ex}\n")
        return 1
    for line in _lines(stats):
        sys.stdout.write(f"{line}\n")
    for warning in stats.warnings:
        sys.stderr.write(f"предупреждение: {warning}\n")
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the command line."""
    parser = argparse.ArgumentParser(
        prog="python -m content_factory.services.sources.main",
        description="Импортировать исходники профилей в пул.",
    )
    parser.add_argument(
        "--sources",
        type=Path,
        help="корень исходников (по умолчанию <data_dir>/sources)",
    )
    return parser.parse_args(argv)


def _absolute(path: Path) -> Path:
    """Resolve a relative command-line path against the working directory."""
    return path.expanduser().resolve()


def _lines(stats: ImportStats) -> list[str]:
    """Format the counters of one import run for the terminal."""
    lines = [
        f"{key}: добавлено {value.added}, обновлено {value.updated}, "
        f"вернулось {value.restored}, без изменений {value.unchanged}, "
        f"дубликатов {value.duplicates}, пропало {value.missing}"
        for key, value in sorted(stats.profiles.items())
    ]
    if not lines:
        lines.append("исходники не найдены")
    if stats.skipped:
        lines.append(f"пропущено файлов: {stats.skipped}")
    return lines


__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
