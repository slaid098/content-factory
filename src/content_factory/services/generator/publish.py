"""Stock publication of finished generator videos."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

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
    destination = (stock_root / niche / profile.language / final.name).resolve()
    if not destination.is_relative_to(stock_root):
        raise GeneratorError(_publish_error(destination))
    if destination.exists():
        raise GeneratorError(_publish_error(destination))
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as ex:
        raise GeneratorError(_publish_error(destination)) from ex
    try:
        os.link(final, destination)
    except FileExistsError as ex:
        raise GeneratorError(_publish_error(destination)) from ex
    except OSError:
        try:
            shutil.copy2(final, destination)
        except OSError as ex:
            raise GeneratorError(_publish_error(destination)) from ex
    return destination


def _publish_error(destination: Path) -> str:
    """Return the user-facing text of a failed stock publication."""
    return f"не удалось положить в сток: {destination}"
