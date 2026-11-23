"""Stock files that can receive generated titles."""

from __future__ import annotations

from pathlib import Path

from content_factory.constants import LANGUAGE_CODE_RE


def stock_files(content_dir: Path) -> list[tuple[str, Path]]:
    """List language directories and their mp4 files in stable order.

    Only directories named with two or three lowercase Latin letters are
    treated as languages; other entries and non-mp4 files are ignored.

    Args:
        content_dir: Root ``content`` directory of the runtime data.

    Returns:
        Pairs of language code and mp4 path, sorted by language and file name.
    """
    if not content_dir.is_dir():
        return []
    files: list[tuple[str, Path]] = []
    for directory in sorted(content_dir.iterdir(), key=lambda path: path.name):
        if not directory.is_dir() or LANGUAGE_CODE_RE.fullmatch(directory.name) is None:
            continue
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            if path.is_file() and path.suffix == ".mp4":
                files.append((directory.name, path))
    return files
