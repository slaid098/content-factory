"""Parsing of human-readable durations."""

from __future__ import annotations

import re

from content_factory.exceptions.errors import ConfigError

_PATTERN = re.compile(r"^(?P<value>\d+(?:\.\d+)?)(?P<unit>[smh])$")
_MULTIPLIERS = {"s": 1.0, "m": 60.0, "h": 3600.0}


def parse_duration(value: str) -> float:
    """Convert a duration such as ``45s``, ``30m`` or ``6h`` to seconds."""
    if not isinstance(value, str):
        raise ConfigError(f"ожидалась строка длительности, получено: {value!r}")
    match = _PATTERN.fullmatch(value.strip())
    if match is None:
        raise ConfigError(f"не удалось разобрать длительность: {value!r}")
    return float(match["value"]) * _MULTIPLIERS[match["unit"]]
