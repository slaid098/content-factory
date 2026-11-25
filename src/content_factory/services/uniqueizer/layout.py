"""Filesystem layout of the uniqueizer sources and of the copy buffer."""

from __future__ import annotations

from pathlib import Path

from content_factory.constants import BUFFER_DIR, LANGUAGE_CODE_RE, SOURCES_DIR, UNIQUE_DIR
from content_factory.services.profiles import is_safe_profile_id


def profile_slug(profile_id: str) -> str:
    """Return the directory name of a profile id such as ``dating_profile_1``.

    Raises:
        ValueError: The id is not a safe ``<category>/<name>`` pair.
    """
    if not is_safe_profile_id(profile_id):
        raise ValueError(f"небезопасный id профиля: {profile_id!r}")
    return profile_id.replace("/", "_")


def source_directory(data_dir: Path, language: str, profile_id: str) -> Path:
    """Return ``<data_dir>/sources/<language>/<profile_slug>``.

    Raises:
        ValueError: The language or the profile id has an invalid shape.
    """
    return data_dir / SOURCES_DIR / _language(language) / profile_slug(profile_id)


def buffer_directory(data_dir: Path, language: str, profile_id: str) -> Path:
    """Return ``<data_dir>/unique/<language>/<profile_slug>/buffer``.

    Raises:
        ValueError: The language or the profile id has an invalid shape.
    """
    return data_dir / UNIQUE_DIR / _language(language) / profile_slug(profile_id) / BUFFER_DIR


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


__all__ = ["buffer_directory", "copy_name", "profile_slug", "source_directory"]
