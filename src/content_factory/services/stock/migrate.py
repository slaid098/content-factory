"""Move the legacy flat stock into the ``general`` niche.

Run it once after a deployment that introduces the nested stock layout::

    python -m content_factory.services.stock.migrate /app/app_data

The command prints the number of moved files. Running it again prints zero.
"""

from __future__ import annotations

import sys
from pathlib import Path

from content_factory.services.stock.layout import migrate_flat_layout


def main(argv: list[str] | None = None) -> int:
    """Move flat stock files and print how many were moved."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m content_factory.services.stock.migrate <data_dir>", file=sys.stderr)
        return 2
    moved = migrate_flat_layout(Path(args[0]))
    print(moved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
