"""Loguru bridge shared by every entry point."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from loguru import Logger


def configure_logger(level: str = "INFO") -> None:
    """Replace the default handler with a stderr handler at the given level."""
    logger.remove()
    logger.add(sys.stderr, level=level, backtrace=False, diagnose=False)


def get_logger(name: str) -> Logger:
    """Return a logger bound to the component name."""
    return logger.bind(component=name)
