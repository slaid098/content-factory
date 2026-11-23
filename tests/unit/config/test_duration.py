from __future__ import annotations

import pytest

from content_factory.exceptions import ConfigError
from content_factory.utils.duration import parse_duration


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("45s", 45.0),
        ("30m", 1800.0),
        ("6h", 21600.0),
        ("1.5h", 5400.0),
        (" 10s ", 10.0),
    ],
)
def test_parse_duration(value: str, expected: float) -> None:
    assert parse_duration(value) == expected


@pytest.mark.parametrize("value", ["6 часов", "45", "h", "", "10d", "6h30m"])
def test_parse_duration_rejects_unknown_format(value: str) -> None:
    with pytest.raises(ConfigError, match="длительность"):
        parse_duration(value)


def test_parse_duration_rejects_non_string() -> None:
    with pytest.raises(ConfigError, match="строка длительности"):
        parse_duration(60)  # type: ignore[arg-type]
