"""Shared pytest configuration."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Требует 90% покрытия только от полного прогона.

    Порог из addopts относится ко всему src. Точечный запуск одного файла
    (например, tests/test_deploy_contract.py) физически не покрывает пакет,
    поэтому для выборки проверку отключаем.
    """
    selected = [Path(path) for path in config.args]
    if not selected or any(path.is_dir() for path in selected):
        return
    # pytest-cov создаёт плагин на early_config и хранит порог в своей копии
    # namespace, поэтому config.option уже не влияет на итоговую проверку.
    plugin = config.pluginmanager.getplugin("_cov")
    if plugin is not None:
        plugin.options.cov_fail_under = 0
