"""Loader for ``app_data/config/config.yaml``."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

import yaml
from loguru import logger

from content_factory.config.defaults import CONFIG_TEMPLATE, DEFAULT_CONFIG
from content_factory.exceptions.errors import ConfigError
from content_factory.schemas.config.runtime import (
    AlertsConfig,
    CaptchaConfig,
    DeliveryConfig,
    DownloadConfig,
    FilterConfig,
    GeneratorConfig,
    LanguageConfig,
    RunConfig,
    RuntimeConfig,
    SearchConfig,
)
from content_factory.utils.duration import parse_duration


def load_runtime_config(path: Path, *, seed: bool = True) -> RuntimeConfig:
    """Load the runtime config, seeding the default file on first start.

    Args:
        path: Path to ``config.yaml``.
        seed: Create the file from the built-in template when it is missing.
            Read-only callers pass ``False`` so a request never writes.
    """
    if seed:
        _seed(path)
        raw = _read(path)
    else:
        raw = _read(path) if path.exists() else {}
    return validate_runtime_config(raw)


def default_config() -> RuntimeConfig:
    """Build the built-in runtime configuration from ``DEFAULT_CONFIG``.

    The raw mapping stays the only source of default values: the typed
    configuration, the seeded template and the settings editor all follow it.
    """
    return validate_runtime_config(DEFAULT_CONFIG)


def validate_runtime_config(raw: Mapping[str, Any]) -> RuntimeConfig:
    """Validate a raw runtime mapping merged over the built-in defaults.

    The same merge and range checks run for the file loader and the settings
    editor, so an edited document cannot take a shortcut around validation.
    No file is read or written.

    Args:
        raw: Parsed YAML mapping, usually the content of ``config.yaml``.

    Raises:
        ConfigError: A key is unknown, a value has the wrong type or a value
            violates a range or a cross-field rule.
    """
    return _build(_merge(DEFAULT_CONFIG, raw, prefix=""))


def _seed(path: Path) -> None:
    """Create the config file from the template when it does not exist."""
    if path.exists():
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as handle:
            handle.write(CONFIG_TEMPLATE)
    except FileExistsError:
        logger.bind(path=str(path)).debug(
            "Файл конфигурации уже создан другим процессом {}",
            path.name,
        )
        return
    except OSError as ex:
        raise ConfigError(f"не удалось создать файл конфигурации {path}: {ex}") from ex


def _read(path: Path) -> Mapping[str, Any]:
    """Read the YAML document; an empty file means "use defaults"."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as ex:
        raise ConfigError(f"не удалось прочитать файл конфигурации {path}: {ex}") from ex
    if not text.strip():
        return {}
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as ex:
        raise ConfigError(f"не удалось разобрать YAML {path}: {ex}") from ex
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: ожидался словарь верхнего уровня")
    return cast(dict[str, Any], data)


def _merge(
    defaults: Mapping[str, Any],
    override: Mapping[str, Any],
    prefix: str,
) -> dict[str, Any]:
    """Recursively merge *override* into *defaults*, rejecting unknown keys."""
    result = dict(defaults)
    for key, value in override.items():
        if key not in defaults:
            if prefix == "languages.":
                known = ", ".join(sorted(defaults))
                raise ConfigError(f"неизвестный язык: {key} (доступны: {known})")
            raise ConfigError(f"неизвестный ключ конфигурации: {prefix}{key}")
        default_value = defaults[key]
        if isinstance(default_value, Mapping):
            if not isinstance(value, Mapping):
                raise ConfigError(f"{prefix}{key}: ожидался словарь")
            result[key] = _merge(default_value, value, f"{prefix}{key}.")
        else:
            result[key] = value
    return result


def _build(data: Mapping[str, Any]) -> RuntimeConfig:
    """Validate the merged mapping and build the typed configuration."""
    run = cast(Mapping[str, Any], data["run"])
    search = cast(Mapping[str, Any], data["search"])
    filter_section = cast(Mapping[str, Any], data["filter"])
    download = cast(Mapping[str, Any], data["download"])
    captcha = cast(Mapping[str, Any], data["captcha"])
    alerts = cast(Mapping[str, Any], data["alerts"])
    delivery = cast(Mapping[str, Any], data["delivery"])
    languages = cast(Mapping[str, Mapping[str, Any]], data["languages"])

    config = RuntimeConfig(
        run=RunConfig(
            batch=_int(run, "batch", "run.batch", minimum=1, maximum=1000),
            interval=_duration(run, "interval", "run.interval"),
            max_duration=_duration(run, "max_duration", "run.max_duration"),
            pause_between_languages=_duration(
                run, "pause_between_languages", "run.pause_between_languages"
            ),
        ),
        search=SearchConfig(
            max_queries=_int(
                search,
                "max_queries",
                "search.max_queries",
                minimum=1,
                maximum=500,
            ),
            pause=_duration(search, "pause", "search.pause"),
        ),
        filter=FilterConfig(
            max=_int(filter_section, "max", "filter.max", minimum=1, maximum=10000),
            pause=_duration(filter_section, "pause", "filter.pause"),
        ),
        download=DownloadConfig(
            workers=_int(download, "workers", "download.workers", minimum=1, maximum=8),
            pause=_duration(download, "pause", "download.pause"),
        ),
        captcha=CaptchaConfig(
            streak=_int(captcha, "streak", "captcha.streak", minimum=1),
            pause=_duration(captcha, "pause", "captcha.pause"),
            long_pause=_duration(captcha, "long_pause", "captcha.long_pause"),
        ),
        alerts=AlertsConfig(cooldown=_duration(alerts, "cooldown", "alerts.cooldown")),
        languages={code: _language(code, value) for code, value in languages.items()},
        generator=_generator_config(data),
        delivery=DeliveryConfig(
            max_count=_int(
                delivery,
                "max_count",
                "delivery.max_count",
                minimum=1,
                maximum=300,
            )
        ),
    )
    if config.run.pause_between_languages > config.run.max_duration:
        raise ConfigError("run.pause_between_languages не может превышать run.max_duration")
    return config


def _generator_config(data: Mapping[str, Any]) -> GeneratorConfig:
    """Build the global generator refill settings with range checks."""
    section = cast(Mapping[str, Any], data["generator"])
    target = _int(section, "target", "generator.target", minimum=1, maximum=1000)
    refill_at = _int(section, "refill_at", "generator.refill_at", minimum=0)
    if refill_at >= target:
        raise ConfigError(
            f"generator.refill_at должен быть меньше generator.target: {refill_at} >= {target}"
        )
    return GeneratorConfig(target=target, refill_at=refill_at)


def _int(
    section: Mapping[str, Any],
    key: str,
    location: str,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    """Return an integer field, rejecting booleans and out-of-range values."""
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{location}: ожидалось целое число")
    if value < minimum:
        raise ConfigError(f"{location}: значение должно быть не меньше {minimum}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{location}: значение должно быть не больше {maximum}")
    return value


def _duration(section: Mapping[str, Any], key: str, location: str) -> float:
    """Return a positive duration field converted to seconds."""
    value = section[key]
    if not isinstance(value, str):
        raise ConfigError(f"{location}: ожидалась длительность строкой, например 6h")
    try:
        seconds = parse_duration(value)
    except ConfigError as ex:
        raise ConfigError(f"{location}: {ex}") from ex
    if seconds <= 0:
        raise ConfigError(f"{location}: длительность должна быть больше нуля")
    return seconds


def _language(code: str, section: Mapping[str, Any]) -> LanguageConfig:
    """Build the configuration of one language."""
    enabled = section["enabled"]
    if not isinstance(enabled, bool):
        raise ConfigError(f"languages.{code}.enabled: ожидалось true или false")
    return LanguageConfig(
        enabled=enabled,
        stock_target=_int(
            section,
            "stock_target",
            f"languages.{code}.stock_target",
            minimum=0,
            maximum=100000,
        ),
    )
