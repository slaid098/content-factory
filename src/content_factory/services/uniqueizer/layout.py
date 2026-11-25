"""Filesystem layout of the uniqueizer sources and of the copy buffer."""

from __future__ import annotations

from pathlib import Path

from content_factory.constants import BUFFER_DIR, LANGUAGE_CODE_RE, SOURCES_DIR, UNIQUE_DIR
from content_factory.services.profiles import is_safe_profile_id, list_profiles


def profile_slugs(data_dir: Path) -> dict[str, str]:
    """Map every unambiguous registry profile slug to its id.

    A slug is the profile id with ``/`` replaced by ``_``. Two different
    valid ids can claim one slug (``dating/profile_1`` and
    ``dating_profile/1``); such a slug is ambiguous and left out.
    """
    groups: dict[str, list[str]] = {}
    for profile in list_profiles(data_dir):
        groups.setdefault(profile.id.replace("/", "_"), []).append(profile.id)
    return {slug: ids[0] for slug, ids in groups.items() if len(ids) == 1}


def profile_slug(data_dir: Path, profile_id: str) -> str:
    """Return the directory name of a profile id such as ``dating_profile_1``.

    The registry disambiguates the lossy ``/`` → ``_`` encoding: a slug is
    usable only when exactly one registered profile owns it and that profile
    is *profile_id*. This keeps two profiles from sharing one source folder
    or one copy buffer.

    Raises:
        ValueError: The id is unsafe, its slug is ambiguous, the slug belongs
            to another registered profile, or the profile is not registered.
    """
    if not is_safe_profile_id(profile_id):
        raise ValueError(f"небезопасный id профиля: {profile_id!r}")
    slug = profile_id.replace("/", "_")
    owners = [
        profile.id for profile in list_profiles(data_dir) if profile.id.replace("/", "_") == slug
    ]
    if len(owners) > 1:
        raise ValueError(f"слаг профиля {slug!r} неоднозначен: {', '.join(owners)}")
    if not owners:
        raise ValueError(f"профиль {profile_id!r} не найден в реестре")
    if owners[0] != profile_id:
        raise ValueError(f"каталог {slug!r} принадлежит профилю {owners[0]!r}, а не {profile_id!r}")
    return slug


def source_directory(data_dir: Path, language: str, profile_id: str) -> Path:
    """Return ``<data_dir>/sources/<language>/<profile_slug>``.

    Raises:
        ValueError: The language or the profile id has an invalid or
            ambiguous shape.
    """
    return data_dir / SOURCES_DIR / _language(language) / profile_slug(data_dir, profile_id)


def buffer_directory(data_dir: Path, language: str, profile_id: str) -> Path:
    """Return ``<data_dir>/unique/<language>/<profile_slug>/buffer``.

    Raises:
        ValueError: The language or the profile id has an invalid or
            ambiguous shape.
    """
    return (
        data_dir
        / UNIQUE_DIR
        / _language(language)
        / profile_slug(data_dir, profile_id)
        / BUFFER_DIR
    )


def copy_name(source_name: str, copy_index: int) -> str:
    """Return the deterministic file name of one ready copy.

    Args:
        source_name: File name of the source such as ``clip.mp4``.
        copy_index: One-based index of the copy for that source.

    Returns:
        ``<source_stem>_<copy_index:03d>.mp4``.

    Raises:
        ValueError: The copy index is not positive.
    """
    if copy_index <= 0:
        raise ValueError("copy_index должен быть больше нуля")
    return f"{Path(source_name).stem}_{copy_index:03d}.mp4"


def _language(language: str) -> str:
    """Return a validated language code.

    Raises:
        ValueError: The code is not two or three lowercase Latin letters.
    """
    if LANGUAGE_CODE_RE.fullmatch(language) is None:
        raise ValueError(f"недопустимый язык: {language!r}")
    return language


__all__ = [
    "buffer_directory",
    "copy_name",
    "profile_slug",
    "profile_slugs",
    "source_directory",
]
