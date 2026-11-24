"""Validation of profile fields shared by the registry and the generator."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from content_factory.constants import LANGUAGE_CODE_RE, NICHE_CODE_RE
from content_factory.exceptions.generator import ProfileError


def read_profile_language(data: Mapping[str, Any], label: str) -> str:
    """Return a validated language code; ``ru`` is the default."""
    value = str(data.get("language") or "ru").strip()
    if LANGUAGE_CODE_RE.fullmatch(value) is None:
        raise ProfileError(f"в профиле {label} недопустимый язык: {value!r}")
    return value


def read_profile_niche(data: Mapping[str, Any], label: str, default: str) -> str:
    """Return a validated niche; an empty value falls back to *default*."""
    value = str(data.get("niche") or "").strip() or default
    if NICHE_CODE_RE.fullmatch(value) is None:
        raise ProfileError(f"в профиле {label} недопустимая ниша: {value!r}")
    return value


def read_profile_positive_int(data: Mapping[str, Any], key: str, label: str) -> int:
    """Return a required positive integer field."""
    try:
        value = int(data.get(key, 0))
    except (TypeError, ValueError) as ex:
        raise ProfileError(f"в профиле {label} {key} должен быть числом") from ex
    if value <= 0:
        raise ProfileError(f"в профиле {label} {key} должен быть больше нуля")
    return value


__all__ = ["read_profile_language", "read_profile_niche", "read_profile_positive_int"]
