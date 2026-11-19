from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from content_factory.config.defaults import (
    CONFIG_TEMPLATE,
    DEFAULT_CONFIG,
    default_config,
)
from content_factory.config.runtime import load_runtime_config
from content_factory.exceptions import ConfigError


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _raise_denied(*args: object, **kwargs: object) -> None:
    raise OSError("permission denied")


_LEAF_KEYS = (
    "batch",
    "interval",
    "max_duration",
    "pause_between_languages",
    "max_queries",
    "pause",
    "max",
    "workers",
    "streak",
    "long_pause",
    "cooldown",
    "enabled",
    "stock_target",
)


def test_seeded_file_comments_every_key(tmp_path: Path) -> None:
    path = tmp_path / "config" / "config.yaml"
    load_runtime_config(path)

    lines = path.read_text(encoding="utf-8").splitlines()
    key_lines = [
        (index, line)
        for index, line in enumerate(lines)
        if line.strip().startswith(tuple(f"{key}:" for key in _LEAF_KEYS))
    ]

    assert len(key_lines) == 18
    for index, line in key_lines:
        assert lines[index - 1].strip().startswith("#"), f"нет комментария: {line.strip()}"


def test_seeds_file_and_returns_defaults(tmp_path: Path) -> None:
    path = tmp_path / "config" / "config.yaml"

    config = load_runtime_config(path)

    assert path.is_file()
    assert "batch" in path.read_text(encoding="utf-8")
    assert config == default_config()
    assert config.run.batch == 20
    assert config.languages["ru"].enabled is True
    assert config.languages["en"].enabled is False


def test_template_matches_defaults() -> None:
    assert yaml.safe_load(CONFIG_TEMPLATE) == DEFAULT_CONFIG


def test_existing_file_is_not_rewritten(tmp_path: Path) -> None:
    path = _write(tmp_path / "config.yaml", CONFIG_TEMPLATE)
    load_runtime_config(path)
    mtime = path.stat().st_mtime_ns

    load_runtime_config(path)

    assert path.stat().st_mtime_ns == mtime


def test_empty_file_uses_defaults_without_rewrite(tmp_path: Path) -> None:
    path = _write(tmp_path / "config.yaml", "")

    config = load_runtime_config(path)

    assert config == default_config()
    assert path.read_text(encoding="utf-8") == ""


def test_null_document_uses_defaults(tmp_path: Path) -> None:
    path = _write(tmp_path / "config.yaml", "null\n")

    assert load_runtime_config(path) == default_config()


def test_partial_file_merges_defaults(tmp_path: Path) -> None:
    path = _write(tmp_path / "config.yaml", "run:\n  batch: 5\n")

    config = load_runtime_config(path)

    assert config.run.batch == 5
    assert config.run.interval == default_config().run.interval
    assert config.languages == default_config().languages


def test_full_override(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "config.yaml",
        """
run:
  batch: 7
  interval: "1h"
  max_duration: "30m"
  pause_between_languages: "1m"
search:
  max_queries: 10
  pause: "5s"
filter:
  max: 11
  pause: "6s"
download:
  workers: 1
  pause: "7s"
captcha:
  streak: 2
  pause: "8m"
  long_pause: "9h"
alerts:
  cooldown: "10s"
languages:
  ru:
    enabled: false
    stock_target: 3
  en:
    enabled: true
    stock_target: 4
""",
    )

    config = load_runtime_config(path)

    assert config.run.batch == 7
    assert config.run.interval == 3600.0
    assert config.run.max_duration == 1800.0
    assert config.run.pause_between_languages == 60.0
    assert config.search.max_queries == 10
    assert config.search.pause == 5.0
    assert config.filter.max == 11
    assert config.filter.pause == 6.0
    assert config.download.workers == 1
    assert config.download.pause == 7.0
    assert config.captcha.streak == 2
    assert config.captcha.pause == 480.0
    assert config.captcha.long_pause == 32400.0
    assert config.alerts.cooldown == 10.0
    assert config.languages["ru"].enabled is False
    assert config.languages["ru"].stock_target == 3
    assert config.languages["en"].enabled is True
    assert config.languages["en"].stock_target == 4


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("run:\n  retries: 2\n", "run.retries"),
        ("unknown: 1\n", "unknown"),
        ("run: [1, 2]\n", "run: ожидался словарь"),
        ("languages:\n  de:\n    enabled: true\n    stock_target: 5\n", "язык: de"),
        ("languages:\n  ru:\n    mode: fast\n", "languages.ru.mode"),
        ("run:\n  batch: 0\n", "run.batch"),
        ("run:\n  batch: -1\n", "run.batch"),
        ("run:\n  batch: true\n", "run.batch"),
        ('run:\n  batch: "20"\n', "run.batch"),
        ("run:\n  interval: 3600\n", "run.interval"),
        ("run:\n  interval: 6 часов\n", "run.interval"),
        ('run:\n  interval: "0h"\n', "run.interval"),
        (
            'run:\n  max_duration: "1h"\n  pause_between_languages: "2h"\n',
            "pause_between_languages",
        ),
        ("search:\n  max_queries: 0\n", "search.max_queries"),
        ("filter:\n  max: 0\n", "filter.max"),
        ("download:\n  workers: 0\n", "download.workers"),
        ("captcha:\n  streak: 0\n", "captcha.streak"),
        ("languages:\n  ru:\n    stock_target: -1\n", "languages.ru.stock_target"),
        ("languages:\n  ru:\n    enabled: 1\n", "languages.ru.enabled"),
    ],
)
def test_invalid_values_are_rejected(tmp_path: Path, text: str, match: str) -> None:
    path = _write(tmp_path / "config.yaml", text)

    with pytest.raises(ConfigError, match=match):
        load_runtime_config(path)


def test_broken_yaml_reports_path(tmp_path: Path) -> None:
    path = _write(tmp_path / "config.yaml", "run: [\n")

    with pytest.raises(ConfigError, match="config.yaml"):
        load_runtime_config(path)


def test_non_mapping_document_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path / "config.yaml", "- 1\n- 2\n")

    with pytest.raises(ConfigError, match="словарь верхнего уровня"):
        load_runtime_config(path)


def test_seed_keeps_file_created_by_another_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path / "config.yaml", "run:\n  batch: 7\n")
    monkeypatch.setattr(Path, "exists", lambda self: False)

    config = load_runtime_config(path)

    assert config.run.batch == 7


def test_seed_filesystem_error_is_wrapped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "config" / "config.yaml"
    monkeypatch.setattr(Path, "mkdir", _raise_denied)

    with pytest.raises(ConfigError, match="не удалось создать"):
        load_runtime_config(path)


def test_read_filesystem_error_is_wrapped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write(tmp_path / "config.yaml", "run:\n  batch: 1\n")
    monkeypatch.setattr(Path, "read_text", _raise_denied)

    with pytest.raises(ConfigError, match="не удалось прочитать"):
        load_runtime_config(path)
