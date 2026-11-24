"""Filesystem helpers shared by the capsules."""

from __future__ import annotations

from pathlib import Path

from loguru import logger


def child_directories(root: Path) -> list[Path]:
    """Return child directories in name order, logging a read error."""
    try:
        entries = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError as ex:
        logger.bind().warning("Не удалось прочитать каталог {}: {}", root, ex)
        return []
    return [entry for entry in entries if entry.is_dir()]


__all__ = ["child_directories"]
