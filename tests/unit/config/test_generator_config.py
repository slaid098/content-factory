"""Global generator refill settings in ``config.yaml``."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from content_factory.config.defaults import CONFIG_TEMPLATE, DEFAULT_CONFIG
from content_factory.config.runtime import (
    default_config,
    load_runtime_config,
    validate_runtime_config,
)
from content_factory.exceptions import ConfigError
from content_factory.schemas.config.runtime import GeneratorConfig


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_default_generator_values_are_target_20_and_refill_2() -> None:
    assert DEFAULT_CONFIG["generator"] == {"target": 20, "refill_at": 2}
    assert yaml.safe_load(CONFIG_TEMPLATE)["generator"] == {"target": 20, "refill_at": 2}
    assert default_config().generator == GeneratorConfig(target=20, refill_at=2)


def test_generator_section_is_read_from_the_file(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "config.yaml",
        "generator:\n  target: 7\n  refill_at: 3\n",
    )

    config = load_runtime_config(path)

    assert config.generator == GeneratorConfig(target=7, refill_at=3)


def test_missing_generator_section_uses_defaults(tmp_path: Path) -> None:
    path = _write(tmp_path / "config.yaml", "run:\n  batch: 5\n")

    config = load_runtime_config(path)

    assert config.generator == GeneratorConfig(target=20, refill_at=2)


def test_partial_generator_section_merges_defaults(tmp_path: Path) -> None:
    path = _write(tmp_path / "config.yaml", "generator:\n  target: 7\n")

    config = load_runtime_config(path)

    assert config.generator == GeneratorConfig(target=7, refill_at=2)


def test_validate_runtime_config_uses_generator_defaults() -> None:
    assert validate_runtime_config({}).generator == GeneratorConfig(target=20, refill_at=2)


def test_refill_at_zero_is_allowed(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "config.yaml",
        "generator:\n  target: 5\n  refill_at: 0\n",
    )

    assert load_runtime_config(path).generator.refill_at == 0


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("generator:\n  target: 0\n", "generator.target"),
        ("generator:\n  target: 1001\n", "generator.target"),
        ("generator:\n  target: true\n", "generator.target"),
        ('generator:\n  target: "20"\n', "generator.target"),
        ("generator:\n  refill_at: -1\n", "generator.refill_at"),
        ("generator:\n  refill_at: true\n", "generator.refill_at"),
        ('generator:\n  refill_at: "2"\n', "generator.refill_at"),
        ("generator:\n  target: 5\n  refill_at: 5\n", "generator.refill_at"),
        ("generator:\n  target: 5\n  refill_at: 6\n", "generator.refill_at"),
        ("generator:\n  unknown: 1\n", "generator.unknown"),
    ],
)
def test_invalid_generator_values_are_rejected(
    tmp_path: Path,
    text: str,
    match: str,
) -> None:
    path = _write(tmp_path / "config.yaml", text)

    with pytest.raises(ConfigError, match=match):
        load_runtime_config(path)


def test_generator_must_be_a_mapping(tmp_path: Path) -> None:
    path = _write(tmp_path / "config.yaml", "generator: [1, 2]\n")

    with pytest.raises(ConfigError, match="generator: ожидался словарь"):
        load_runtime_config(path)
