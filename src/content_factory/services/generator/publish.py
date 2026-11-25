"""Stock publication of finished generator videos and photo posts."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from loguru import logger

from content_factory.constants import CONTENT_DIR, LANGUAGE_CODE_RE, NICHE_CODE_RE
from content_factory.exceptions.generator import GeneratorError
from content_factory.services.generator.profiles import _Profile, profile_niche


def publish_stock(profile: _Profile, final: Path, data_root: Path) -> Path:
    """Publish one final video into ``content/<niche>/<language>/``.

    A hard link keeps the video in ``generated/`` without spending disk space.
    When linking is impossible, the file is copied instead. An existing stock
    file is never overwritten: that is a publication error. The niche and the
    language are validated again here, and the resolved destination must stay
    inside the stock root, so a malformed profile cannot write anywhere else.

    Args:
        profile: Profile that produced the video; an empty niche falls back
            to the profile category.
        final: Finished mp4 inside the run ``final`` directory.
        data_root: Root ``app_data`` directory.

    Returns:
        Path of the published stock file.

    Raises:
        GeneratorError: The niche or language is invalid, the destination
            escapes the stock root, the stock file already exists, the
            destination cannot be created or neither linking nor copying
            succeeded.
    """
    destination = _stock_path(profile, final.name, data_root)
    _place(final, destination)
    return destination


def publish_photo(profile: _Profile, final: Path, data_root: Path) -> Path:
    """Publish a photo and its text sidecar as one stock pair.

    The photo keeps its ``.jpg`` name; the sidecar is linked or copied next to
    it as ``.json``. Both destinations must be free before anything is placed,
    and a failure of the second file removes the first one, so a published
    post is always a complete pair. The source files stay in ``generated/``.

    Args:
        profile: Profile that produced the photo.
        final: Finished JPEG inside the run ``final`` directory.
        data_root: Root ``app_data`` directory.

    Returns:
        Path of the published stock photo.

    Raises:
        GeneratorError: The sidecar is missing, the niche or language is
            invalid, a destination escapes the stock root or already exists,
            or either file cannot be linked or copied.
    """
    sidecar = final.with_suffix(".json")
    if not sidecar.is_file():
        raise GeneratorError(f"не удалось положить в сток: нет текста поста {sidecar}")
    destination = _stock_path(profile, final.name, data_root)
    sidecar_destination = _stock_path(profile, sidecar.name, data_root)
    _place(final, destination)
    try:
        _place(sidecar, sidecar_destination)
    except GeneratorError:
        _remove(destination)
        raise
    return destination


def _stock_path(profile: _Profile, name: str, data_root: Path) -> Path:
    """Return a free, validated destination inside the stock root.

    Raises:
        GeneratorError: The niche or language is invalid, the destination
            escapes the stock root, it already exists or its directory cannot
            be created.
    """
    niche = profile_niche(profile)
    if (
        NICHE_CODE_RE.fullmatch(niche) is None
        or LANGUAGE_CODE_RE.fullmatch(profile.language) is None
    ):
        raise GeneratorError(
            "не удалось положить в сток: недопустимая ниша или язык профиля "
            f"{niche!r}/{profile.language!r}"
        )
    stock_root = (data_root / CONTENT_DIR).resolve()
    destination = (stock_root / niche / profile.language / name).resolve()
    if not destination.is_relative_to(stock_root):
        raise GeneratorError(_publish_error(destination))
    if destination.exists():
        raise GeneratorError(_publish_error(destination))
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as ex:
        raise GeneratorError(_publish_error(destination)) from ex
    return destination


def _place(source: Path, destination: Path) -> None:
    """Link *source* to *destination*, falling back to a copy.

    A failed copy removes the partial destination it may have created.

    Raises:
        GeneratorError: The destination exists or neither linking nor copying
            succeeded.
    """
    try:
        os.link(source, destination)
    except FileExistsError as ex:
        raise GeneratorError(_publish_error(destination)) from ex
    except OSError:
        try:
            shutil.copy2(source, destination)
        except OSError as ex:
            _remove(destination)
            raise GeneratorError(_publish_error(destination)) from ex


def _remove(path: Path) -> None:
    """Remove a half-published file, logging a failure instead of masking it."""
    try:
        path.unlink(missing_ok=True)
    except OSError as ex:
        logger.bind(path=str(path), error=ex, error_type=type(ex).__name__).warning(
            "Не удалось убрать недописанный файл стока {}", path
        )


def _publish_error(destination: Path) -> str:
    """Return the user-facing text of a failed stock publication."""
    return f"не удалось положить в сток: {destination}"
