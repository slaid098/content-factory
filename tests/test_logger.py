from __future__ import annotations

from loguru import logger

from content_factory.config.logger import configure_logger, get_logger


def test_configure_logger_accepts_supported_levels() -> None:
    configure_logger("DEBUG")
    configure_logger("INFO")


def test_get_logger_returns_bound_copy() -> None:
    bound = get_logger("tests")

    assert bound is not logger
