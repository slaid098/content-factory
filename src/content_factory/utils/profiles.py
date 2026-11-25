"""Validation of profile fields shared by the registry and the generator."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from content_factory.constants import LANGUAGE_CODE_RE, NICHE_CODE_RE
from content_factory.exceptions.generator import ProfileError
from content_factory.schemas.profiles.delivery import DeliveryInfo, TitleConfig, TitleMode

_HASHTAG_RE = re.compile(r"[\w][\w-]*")
_MAX_TITLE_LENGTH = 100
_MAX_HASHTAGS = 10
_MISSING = object()


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


def read_profile_delivery(data: Mapping[str, Any], label: str) -> DeliveryInfo:
    """Return the optional delivery policy of one profile.

    A missing ``delivery`` block, ``null`` and an empty mapping all mean the
    backward-compatible policy: the title of every video comes from ``titles``
    and the sidecar has no hashtags.

    Args:
        data: Parsed ``profile.yaml``.
        label: Profile id used in error messages.

    Returns:
        Validated title policy and hashtags.

    Raises:
        ProfileError: The block, its title or its hashtags are malformed.
    """
    raw = data.get("delivery")
    if raw is None:
        return DeliveryInfo()
    if not isinstance(raw, Mapping):
        raise ProfileError(f"в профиле {label} delivery должен быть словарём")
    title = raw.get("title", _MISSING)
    hashtags = raw.get("hashtags", _MISSING)
    return DeliveryInfo(
        title=TitleConfig() if title is _MISSING else _read_title_config(title, label),
        hashtags=() if hashtags is _MISSING else _read_hashtags(hashtags, label),
    )


def _read_title_config(raw: object, label: str) -> TitleConfig:
    """Validate the ``delivery.title`` object."""
    if not isinstance(raw, Mapping):
        raise ProfileError(f"в профиле {label} delivery.title должен быть словарём")
    mode = _read_title_mode(raw.get("mode", TitleMode.GENERATED.value), label)
    if mode is TitleMode.GENERATED:
        if "value" in raw:
            raise ProfileError(f"в профиле {label} generated-название не принимает value")
        return TitleConfig()
    return TitleConfig(mode=mode, value=_read_fixed_title(raw.get("value"), label))


def _read_title_mode(raw: object, label: str) -> TitleMode:
    """Return a known title mode."""
    if not isinstance(raw, str):
        raise ProfileError(f"в профиле {label} неизвестный режим названия: {raw!r}")
    try:
        return TitleMode(raw)
    except ValueError as ex:
        raise ProfileError(f"в профиле {label} неизвестный режим названия: {raw!r}") from ex


def _read_fixed_title(raw: object, label: str) -> str:
    """Return the collapsed title of a ``fixed`` profile."""
    if not isinstance(raw, str):
        raise ProfileError(f"в профиле {label} fixed-название должно быть строкой")
    value = " ".join(raw.split())
    if not value or len(value) > _MAX_TITLE_LENGTH:
        raise ProfileError(
            f"в профиле {label} fixed-название должно быть длиной 1..{_MAX_TITLE_LENGTH} символов"
        )
    return value


def _read_hashtags(raw: object, label: str) -> tuple[str, ...]:
    """Validate hashtags, keeping ``#`` and dropping casefold duplicates."""
    if not isinstance(raw, list):
        raise ProfileError(f"в профиле {label} hashtags должен быть списком")
    if len(raw) > _MAX_HASHTAGS:
        raise ProfileError(f"в профиле {label} hashtags должен содержать не больше {_MAX_HASHTAGS}")
    tags: list[str] = []
    seen: set[str] = set()
    for item in raw:
        tag = _read_hashtag(item, label)
        key = tag.casefold()
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag)
    return tuple(tags)


def _read_hashtag(raw: object, label: str) -> str:
    """Return one hashtag in its stored ``#tag`` form."""
    if not isinstance(raw, str):
        raise ProfileError(f"в профиле {label} hashtag должен быть строкой")
    value = raw.strip()
    body = value[1:] if value.startswith("#") else value
    if _HASHTAG_RE.fullmatch(body) is None:
        raise ProfileError(f"в профиле {label} недопустимый hashtag: {raw!r}")
    return f"#{body}"


__all__ = [
    "read_profile_delivery",
    "read_profile_language",
    "read_profile_niche",
    "read_profile_positive_int",
]
