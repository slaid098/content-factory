"""Move the legacy flat stock into the ``general`` niche.

Run it once after a deployment that introduces the nested stock layout::

    python -m content_factory.services.stock.migrate /app/app_data

The command logs the number of moved files. Running it again moves zero.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from loguru import logger

from content_factory.services.stock.layout import migrate_flat_layout


def main(argv: list[str] | None = None) -> int:
    """Move flat stock files and return how many were moved."""
    parser = argparse.ArgumentParser(
        prog="python -m content_factory.services.stock.migrate",
        description="Перенести плоский сток в content/general/<язык>.",
    )
    parser.add_argument("data_dir", type=Path, help="корень app_data")
    args = parser.parse_args(argv)
    moved = migrate_flat_layout(args.data_dir)
    logger.bind(data_dir=str(args.data_dir)).info("Перенесено файлов стока: {}", moved)
    return moved


if __name__ == "__main__":
    main()
