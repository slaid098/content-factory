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
    created = _stat_or_none(destination)
    try:
        _place(sidecar, sidecar_destination)
    except GeneratorError:
        _remove_if_same(destination, created)
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
    """Link *source* to *destination*, falling back to an exclusive copy.

    A destination created by the copy fallback is removed when copying fails,
    so a failed publication never leaves a partial file. An existing
    destination is never overwritten, even when the hard link fails for a
    reason other than a name clash.

    Raises:
        GeneratorError: The destination exists or neither linking nor copying
            succeeded.
    """
    try:
        os.link(source, destination)
    except FileExistsError as ex:
        raise GeneratorError(_publish_error(destination)) from ex
    except OSError:
        _copy_exclusive(source, destination)


def _copy_exclusive(source: Path, destination: Path) -> None:
    """Copy *source* into a destination created exclusively by this call.

    ``open("xb")`` refuses an existing name, so the copy fallback cannot
    overwrite a file that appeared after the earlier free-name check.

    Raises:
        GeneratorError: The destination exists or cannot be read or written.
    """
    try:
        src = source.open("rb")
    except OSError as ex:
        raise GeneratorError(_publish_error(destination)) from ex
    try:
        try:
            dst = destination.open("xb")
        except FileExistsError as ex:
            raise GeneratorError(_publish_error(destination)) from ex
        except OSError as ex:
            raise GeneratorError(_publish_error(destination)) from ex
        try:
            created: os.stat_result | None = None
            with dst:
                created = os.fstat(dst.fileno())
                shutil.copyfileobj(src, dst)
        except OSError as ex:
            _remove_if_same(destination, created)
            raise GeneratorError(_publish_error(destination)) from ex
    finally:
        src.close()


def _stat_or_none(path: Path) -> os.stat_result | None:
    """Return the stat of a just-created file, logging instead of failing."""
    try:
        return path.stat()
    except OSError as ex:
        logger.bind(path=str(path), error=ex, error_type=type(ex).__name__).warning(
            "Не удалось проверить опубликованный файл {}", path
        )
        return None


def _remove_if_same(path: Path, expected: os.stat_result | None) -> None:
    """Remove *path* only while it is still the file this call created."""
    if expected is None:
        return
    try:
        current = path.stat()
    except FileNotFoundError:
        return
    except OSError as ex:
        logger.bind(path=str(path), error=ex, error_type=type(ex).__name__).warning(
            "Не удалось проверить файл стока перед откатом {}", path
        )
        return
    if (current.st_dev, current.st_ino) != (expected.st_dev, expected.st_ino):
        logger.bind(path=str(path)).warning(
            "Файл стока {} заменён другим — откат его не удаляет", path
        )
        return
    _remove(path)


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
