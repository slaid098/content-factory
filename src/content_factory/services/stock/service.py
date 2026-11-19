"""Stock accounting."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


def count_stock(content_dir: Path, languages: Iterable[str]) -> dict[str, int]:
    """Посчитать готовые mp4-файлы по каталогам языков."""
    return {language: _count_language(content_dir / language) for language in languages}


def _count_language(directory: Path) -> int:
    """Count regular ``*.mp4`` files in one language directory."""
    if not directory.is_dir():
        return 0
    return sum(1 for path in directory.iterdir() if path.is_file() and path.suffix == ".mp4")
