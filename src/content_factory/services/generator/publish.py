"""Stock publication of finished generator videos and photo posts."""

from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import suppress
from pathlib import Path

from loguru import logger

from content_factory.constants import CONTENT_DIR, LANGUAGE_CODE_RE, NICHE_CODE_RE
from content_factory.exceptions.generator import GeneratorError
from content_factory.services.generator.profiles import _Profile, profile_niche

_STOCK_FILE_MODE = 0o644


def publish_stock(profile: _Profile, final: Path, data_root: Path) -> Path:
    """Publish one final video into ``content/<niche>/<language>/``.

    A hard link keeps the video in ``generated/`` without spending disk space.
    When linking is impossible, the file is copied into a hidden temporary file
    next to the destination and linked under its stock name only after the copy
    is complete. On a filesystem without hard links the stock name appears
    before the copy finishes, but an existing file is still never overwritten.
    A copied stock file gets mode ``0644`` so the partner can read it. An
    existing stock file is a publication error. The niche and the language are
    validated again here, and the resolved destination must stay inside the
    stock root, so a malformed profile cannot write anywhere else.

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

    The text sidecar is placed first and the photo last, so a published photo
    is never without its text. Both destinations must be free before anything
    is placed. On a filesystem with hard links each name appears only after its
    copy is complete; without hard links a name appears before its copy
    finishes, but an existing file is still never overwritten. Copied stock
    files get mode ``0644`` so the partner can read them. When the photo cannot
    be placed, the sidecar is removed, but only while it is still the inode
    this call created, so a file replaced by someone else is never deleted.
    The source files stay in ``generated/``.

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
    sidecar_created = _place(sidecar, sidecar_destination)
    try:
        _place(final, destination)
    except GeneratorError:
        _remove_if_same(sidecar_destination, sidecar_created)
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


def _place(source: Path, destination: Path) -> os.stat_result:
    """Link *source* to *destination* and return the inode that was placed.

    The returned stat describes the source inode, taken before linking, so it
    never depends on what the destination name points to later. When a hard
    link is impossible, the source is copied into an exclusive temporary file
    in the destination directory and linked under its final name only after
    the copy is complete; the final name never receives a partial file and
    never replaces an existing one.

    Args:
        source: Finished file inside the run ``final`` directory.
        destination: Free stock path of the file.

    Returns:
        Stat of the inode placed at *destination*.

    Raises:
        GeneratorError: The source cannot be read, the destination exists or
            neither linking nor copying succeeded.
    """
    try:
        created = source.stat()
    except OSError as ex:
        raise GeneratorError(_publish_error(destination)) from ex
    try:
        os.link(source, destination)
    except FileExistsError as ex:
        raise GeneratorError(_publish_error(destination)) from ex
    except OSError:
        return _copy_exclusive(source, destination)
    return created


def _copy_exclusive(source: Path, destination: Path) -> os.stat_result:
    """Copy *source* into a temporary file and place it at *destination*.

    The temporary copy is made readable by the partner (``0644``) regardless
    of the source mode or the process umask, and a hard link then makes the
    destination name appear atomically. On a filesystem without hard links the
    temporary file is copied into an exclusively created destination: there
    the name appears before the copy is complete, but an existing file is
    still never overwritten. The temporary file is removed in every case.

    Args:
        source: Finished file inside the run ``final`` directory.
        destination: Free stock path of the file.

    Returns:
        Stat of the inode placed at *destination*.

    Raises:
        GeneratorError: The source cannot be read, the temporary file cannot
            be written, or the destination already exists.
    """
    try:
        fd, temp_name = tempfile.mkstemp(
            dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp"
        )
    except OSError as ex:
        raise GeneratorError(_publish_error(destination)) from ex
    temp = Path(temp_name)
    try:
        created = _fill_temp(source, fd, destination)
        try:
            os.link(temp, destination)
        except FileExistsError as ex:
            raise GeneratorError(_publish_error(destination)) from ex
        except OSError:
            return _copy_into_new_inode(temp, destination)
        return created
    finally:
        _remove(temp)


def _fill_temp(source: Path, fd: int, destination: Path) -> os.stat_result:
    """Fill a ``mkstemp`` descriptor from *source* and make it stock-readable.

    The descriptor is closed on every path, including a failed ``os.fdopen``.

    Args:
        source: Finished file inside the run ``final`` directory.
        fd: Open descriptor of the temporary file.
        destination: Final stock path, used in error messages.

    Returns:
        Stat of the temporary inode taken before copying.

    Raises:
        GeneratorError: The source cannot be read or the temporary file cannot
            be written.
    """
    try:
        writer = os.fdopen(fd, "wb")
    except OSError as ex:
        with suppress(OSError):
            os.close(fd)
        raise GeneratorError(_publish_error(destination)) from ex
    except BaseException:
        with suppress(OSError):
            os.close(fd)
        raise
    try:
        with writer:
            created = os.fstat(writer.fileno())
            with source.open("rb") as reader:
                shutil.copyfileobj(reader, writer)
            os.fchmod(writer.fileno(), _STOCK_FILE_MODE)
    except OSError as ex:
        raise GeneratorError(_publish_error(destination)) from ex
    return created


def _copy_into_new_inode(source: Path, destination: Path) -> os.stat_result:
    """Copy *source* into an exclusively created *destination*.

    Used when the filesystem does not support hard links. ``open("xb")``
    refuses an existing name, so a concurrent publication is never
    overwritten, and the new inode gets the stock mode ``0644`` before the
    copy starts.

    Args:
        source: Completed temporary file next to the destination.
        destination: Free stock path of the file.

    Returns:
        Stat of the inode created at *destination*.

    Raises:
        GeneratorError: The destination exists or cannot be written.
    """
    try:
        writer = destination.open("xb")
    except FileExistsError as ex:
        raise GeneratorError(_publish_error(destination)) from ex
    except OSError as ex:
        raise GeneratorError(_publish_error(destination)) from ex
    created: os.stat_result | None = None
    completed = False
    try:
        created = os.fstat(writer.fileno())
        os.fchmod(writer.fileno(), _STOCK_FILE_MODE)
        with writer, source.open("rb") as reader:
            shutil.copyfileobj(reader, writer)
        completed = True
        return created
    except OSError as ex:
        raise GeneratorError(_publish_error(destination)) from ex
    finally:
        with suppress(OSError):
            writer.close()
        if not completed and created is not None:
            _remove_if_same(destination, created)


def _remove_if_same(path: Path, expected: os.stat_result) -> None:
    """Remove *path* only while it is still the inode this call created."""
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
