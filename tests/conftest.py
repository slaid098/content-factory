"""Shared pytest configuration."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest
from loguru import logger


def pytest_configure(config: pytest.Config) -> None:
    """Требует 90% покрытия только от полного прогона.

    Порог из addopts относится ко всему src. Точечный запуск файла или
    tier-папки (tests/unit, tests/integration) физически не покрывает пакет,
    поэтому для выборки проверку отключаем.
    """
    selected = [Path(path).resolve() for path in config.args]
    tests_root = Path(__file__).resolve().parent
    full_run = not selected or any(
        path == tests_root or path in tests_root.parents for path in selected
    )
    if full_run:
        return
    # pytest-cov создаёт плагин на early_config и хранит порог в своей копии
    # namespace, поэтому config.option уже не влияет на итоговую проверку.
    plugin = config.pluginmanager.getplugin("_cov")
    if plugin is not None:
        plugin.options.cov_fail_under = 0


@pytest.fixture
def log_records() -> Iterator[list[dict[str, Any]]]:
    """Collect raw loguru records for level, message and bound context checks."""
    records: list[dict[str, Any]] = []
    sink_id = logger.add(lambda message: records.append(message.record), level="DEBUG")
    try:
        yield records
    finally:
        with suppress(ValueError):
            logger.remove(sink_id)
