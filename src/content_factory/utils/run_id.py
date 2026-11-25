"""Validation of generator run ids and of the directory names they use."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from content_factory.exceptions.generator import GeneratorError

_RUN_ID_RE = re.compile(r"[0-9]{8}T[0-9]{6}Z(?:-[0-9]+)?")


class _Named(Protocol):
    """Object that carries a profile name."""

    @property
    def name(self) -> str:
        """Profile name such as ``dating/profile_2``."""
        ...


def profile_slug(profile: _Named) -> str:
    """Return the profile directory name used in output paths."""
    return profile.name.replace("/", "_")


def validate_run_id(run_id: str) -> str:
    """Return *run_id* when it is a timestamp id safe to use in paths.

    Raises:
        GeneratorError: The id is empty or holds characters outside the
            timestamp form, such as separators or ``..``.
    """
    if _RUN_ID_RE.fullmatch(run_id) is None:
        raise GeneratorError(f"недопустимый run_id {run_id!r}: ожидается YYYYMMDDTHHMMSSZ[-N]")
    return run_id


def profile_run_dir(output_dir: Path, language: str, slug: str, run_id: str) -> Path:
    """Return the run directory of one profile, checked to stay inside it.

    Args:
        output_dir: Root of the generated run directories.
        language: Content language of the profile.
        slug: Profile directory name with ``/`` replaced by ``_``.
        run_id: Run id chosen by the flow.

    Returns:
        ``output_dir/<language>/<slug>/<run_id>``.

    Raises:
        GeneratorError: The id is not a timestamp id or the resolved
            directory leaves the profile directory.
    """
    validate_run_id(run_id)
    profile_dir = output_dir / language / slug
    if not (profile_dir / run_id).resolve().is_relative_to(profile_dir.resolve()):
        raise GeneratorError(f"run_id {run_id!r} выводит каталог прогона за пределы профиля")
    return profile_dir / run_id


__all__ = ["profile_run_dir", "profile_slug", "validate_run_id"]
