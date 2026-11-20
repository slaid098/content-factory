"""Shared data for runtime settings tests."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from content_factory.constants import CONFIG_DIR, CONFIG_FILENAME
from content_factory.schemas.settings import RuntimeSettings

DEFAULT_SETTINGS: dict[str, Any] = {
    "languages": [
        {"code": "ru", "enabled": True, "stock_target": 150},
        {"code": "en", "enabled": False, "stock_target": 100},
    ],
    "run": {"batch": 20, "interval": "6h", "max_duration": "45m"},
    "search": {"max_queries": 30},
    "filter": {"max": 60},
    "download": {"workers": 2},
    "delivery": {"max_count": 100},
}


def settings_body(**changes: Any) -> dict[str, Any]:
    """Return the default settings body with shallow section overrides."""
    data = deepcopy(DEFAULT_SETTINGS)
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key].update(value)
        else:
            data[key] = value
    return data


def settings_payload(**changes: Any) -> RuntimeSettings:
    """Return a validated settings payload with section overrides applied."""
    return RuntimeSettings.model_validate(settings_body(**changes))


def config_path(data_dir: Path) -> Path:
    """Return the runtime config path of a test data directory."""
    return data_dir / CONFIG_DIR / CONFIG_FILENAME


def write_config(data_dir: Path, text: str) -> Path:
    """Write a runtime config file, creating its directory."""
    path = config_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
