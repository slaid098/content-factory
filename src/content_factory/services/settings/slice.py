"""Conversion between a YAML document and the editable settings slice."""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping
from typing import Any, cast

from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.scalarstring import DoubleQuotedScalarString, SingleQuotedScalarString

from content_factory.config.defaults import DEFAULT_CONFIG
from content_factory.exceptions.errors import SettingsValidationError
from content_factory.schemas.config.runtime import RuntimeConfig
from content_factory.schemas.settings import (
    DeliverySettings,
    DownloadSettings,
    FilterSettings,
    LanguageSettings,
    RunSettings,
    RuntimeSettings,
    SearchSettings,
)

_EDITABLE_KEYS = {
    "run": ("batch", "interval", "max_duration"),
    "search": ("max_queries",),
    "filter": ("max",),
    "download": ("workers",),
    "delivery": ("max_count",),
}


def settings_from_runtime(
    runtime: RuntimeConfig,
    document: Mapping[str, Any],
) -> RuntimeSettings:
    """Build the safe response slice from validated values and source strings."""
    run = _section(document, "run")
    return RuntimeSettings(
        languages=tuple(
            LanguageSettings(
                code=code,
                enabled=language.enabled,
                stock_target=language.stock_target,
            )
            for code, language in runtime.languages.items()
        ),
        run=RunSettings(
            batch=runtime.run.batch,
            interval=_duration_text(run, "interval", "run.interval"),
            max_duration=_duration_text(run, "max_duration", "run.max_duration"),
        ),
        search=SearchSettings(max_queries=runtime.search.max_queries),
        filter=FilterSettings(max=runtime.filter.max),
        download=DownloadSettings(workers=runtime.download.workers),
        delivery=DeliverySettings(max_count=runtime.delivery.max_count),
    )


def apply_payload(
    document: CommentedMap,
    payload: RuntimeSettings,
    current: RuntimeConfig,
) -> None:
    """Write editable values into the document, preserving everything else.

    Raises:
        SettingsValidationError: The payload adds, removes or repeats a
            language. Languages are only switched and retargeted.
    """
    _check_languages(payload, current)
    run = _ensure_map(document, "run")
    _set(run, "batch", payload.run.batch)
    _set(run, "interval", payload.run.interval)
    _set(run, "max_duration", payload.run.max_duration)

    search = _ensure_map(document, "search")
    _set(search, "max_queries", payload.search.max_queries)

    filter_section = _ensure_map(document, "filter")
    _set(filter_section, "max", payload.filter.max)

    download = _ensure_map(document, "download")
    _set(download, "workers", payload.download.workers)

    delivery = _ensure_map(document, "delivery")
    _set(delivery, "max_count", payload.delivery.max_count)

    languages = _ensure_map(document, "languages")
    for language in payload.languages:
        section = _ensure_map(languages, language.code)
        _set(section, "enabled", language.enabled)
        _set(section, "stock_target", language.stock_target)


def _set(section: CommentedMap, key: str, value: object) -> None:
    """Assign a value only when it changes, keeping untouched formatting."""
    if key in section and section[key] == value:
        return
    section[key] = _quoted_like(section.get(key), value)


def _quoted_like(old: object, value: object) -> object:
    """Keep the old quote style when a replaced scalar was quoted."""
    if not isinstance(value, str):
        return value
    if isinstance(old, DoubleQuotedScalarString):
        return DoubleQuotedScalarString(value)
    if isinstance(old, SingleQuotedScalarString):
        return SingleQuotedScalarString(value)
    return value


def editable_keys_missing(
    document: Mapping[str, Any],
    payload: RuntimeSettings,
) -> bool:
    """Return whether a payload field has no key in the source document yet."""
    for name, keys in _EDITABLE_KEYS.items():
        section = _section(document, name)
        if section is None or any(key not in section for key in keys):
            return True
    languages = _section(document, "languages")
    if languages is None:
        return True
    for language in payload.languages:
        section = languages.get(language.code)
        if not isinstance(section, Mapping):
            return True
        if "enabled" not in section or "stock_target" not in section:
            return True
    return False


def _check_languages(payload: RuntimeSettings, current: RuntimeConfig) -> None:
    """Reject language additions, removals and duplicates."""
    requested = [language.code for language in payload.languages]
    if len(set(requested)) != len(requested):
        raise SettingsValidationError("языки не должны повторяться")
    existing = set(current.languages)
    unknown = sorted(set(requested) - existing)
    if unknown:
        raise SettingsValidationError(f"неизвестный язык: {', '.join(unknown)}")
    removed = sorted(existing - set(requested))
    if removed:
        raise SettingsValidationError(f"язык нельзя удалить: {', '.join(removed)}")


def _section(document: Mapping[str, Any], name: str) -> Mapping[str, Any] | None:
    """Return one top-level section when it is a mapping."""
    value = document.get(name)
    return value if isinstance(value, Mapping) else None


def _ensure_map(document: MutableMapping[str, Any], name: str) -> CommentedMap:
    """Return a mutable mapping for *name*, creating it when missing."""
    value = document.get(name)
    if isinstance(value, CommentedMap):
        return value
    if isinstance(value, Mapping):
        converted = CommentedMap(value)
        document[name] = converted
        return converted
    created = CommentedMap()
    document[name] = created
    return created


def _duration_text(
    section: Mapping[str, Any] | None,
    key: str,
    location: str,
) -> str:
    """Return the duration string from the source document or the default."""
    if section is not None:
        value = section.get(key)
        if isinstance(value, str):
            return value
    _section_name, name = location.split(".", maxsplit=1)
    default = cast(dict[str, Any], DEFAULT_CONFIG[_section_name])
    return cast(str, default[name])
