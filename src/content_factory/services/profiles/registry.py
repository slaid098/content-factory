"""Read-only registry of generator profiles.

The registry deliberately does not call :func:`load_profile`: it exposes the
fields needed to count stock and choose a refill even when a font, a prompt or
an appearance card of the profile is broken. The generator validates those
before it rents a server.

A profile is a directory ``profiles/<category>/<name>/profile.yaml``. The
shared ``profiles/_shared`` directory holds assets for all profiles and is not
a profile itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from content_factory.constants import PROFILES_DIR
from content_factory.exceptions.generator import ProfileError
from content_factory.schemas.profiles.registry import ProfileInfo
from content_factory.utils.filesystem import child_directories
from content_factory.utils.profiles import (
    read_profile_language,
    read_profile_niche,
    read_profile_positive_int,
)

_PROFILE_FILENAME = "profile.yaml"
_SHARED_DIR = "_shared"


def list_profiles(data_dir: Path) -> tuple[ProfileInfo, ...]:
    """List readable profiles of ``<data_dir>/profiles`` in id order.

    A directory without ``profile.yaml`` is not a profile. A broken YAML or an
    invalid field skips only that profile with a warning: the rest stay
    available, and an empty or missing root yields an empty tuple.

    Args:
        data_dir: Root of the runtime data directory, usually ``app_data``.

    Returns:
        Profiles sorted by ``id``.
    """
    profiles: list[ProfileInfo] = []
    for category in child_directories(data_dir / PROFILES_DIR):
        if category.name == _SHARED_DIR:
            continue
        for directory in child_directories(category):
            if not (directory / _PROFILE_FILENAME).is_file():
                continue
            try:
                profiles.append(_read_info(directory, category.name, directory.name))
            except ProfileError as ex:
                logger.bind().warning(
                    "Профиль {}/{} пропущен: {}",
                    category.name,
                    directory.name,
                    ex,
                )
    return tuple(sorted(profiles, key=lambda profile: profile.id))


def get_profile(data_dir: Path, profile_id: str) -> ProfileInfo:
    """Return one profile by ``<category>/<name>``.

    Args:
        data_dir: Root of the runtime data directory, usually ``app_data``.
        profile_id: Profile identifier such as ``dating/profile_2``.

    Returns:
        Registry fields of the profile.

    Raises:
        ProfileError: The id is unsafe, the profile is missing, or
            ``profile.yaml`` is malformed.
    """
    category, name = _split_id(profile_id)
    directory = data_dir / PROFILES_DIR / category / name
    if not directory.is_dir():
        raise ProfileError(f"профиль не найден: {profile_id}")
    return _read_info(directory, category, name)


def _split_id(profile_id: str) -> tuple[str, str]:
    """Split a profile id and reject path escapes and absolute paths."""
    if Path(profile_id).is_absolute() or "\\" in profile_id:
        raise ProfileError(f"небезопасный id профиля: {profile_id!r}")
    parts = profile_id.split("/")
    if len(parts) != 2 or any(part in {"", ".", ".."} for part in parts):
        raise ProfileError(f"id профиля должен быть вида <category>/<name>: {profile_id!r}")
    return parts[0], parts[1]


def _read_info(directory: Path, category: str, name: str) -> ProfileInfo:
    """Build registry fields from one ``profile.yaml``."""
    data = _read_yaml(directory / _PROFILE_FILENAME)
    label = f"{category}/{name}"
    return ProfileInfo(
        id=label,
        category=category,
        name=name,
        language=read_profile_language(data, label),
        niche=read_profile_niche(data, label, category),
        width=read_profile_positive_int(data, "width", label),
        height=read_profile_positive_int(data, "height", label),
        audio=bool(data.get("audio", True)),
        dir=directory,
    )


def _read_yaml(path: Path) -> Mapping[str, Any]:
    """Read ``profile.yaml`` as a mapping."""
    try:
        payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as ex:
        raise ProfileError(f"не удалось прочитать {path}: {ex}") from ex
    except yaml.YAMLError as ex:
        raise ProfileError(f"не удалось разобрать {path}: {ex}") from ex
    if not isinstance(payload, dict):
        raise ProfileError(f"profile.yaml должен быть словарём: {path}")
    return payload


__all__ = ["ProfileInfo", "get_profile", "list_profiles"]
