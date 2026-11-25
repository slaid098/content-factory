"""Delivery archive assembly: media entries, their sidecars and cleanup."""

from __future__ import annotations

import json
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from loguru import logger

from content_factory.schemas.content.delivery import ContentType
from content_factory.services.stock import photo_sidecar, photo_text

_TMP_SUFFIX = ".tmp"
_SIDECAR_SUFFIX = ".json"


def count_archive_files(archive_path: Path) -> int:
    """Return the number of media entries in a built delivery archive.

    Args:
        archive_path: Path to a zip archive written by the delivery service.

    Returns:
        Number of media entries, one per delivered video or photo.

    Raises:
        OSError: The archive cannot be read.
        zipfile.BadZipFile: The file is not a valid zip archive.
    """
    with zipfile.ZipFile(archive_path) as archive:
        return sum(1 for name in archive.namelist() if not name.endswith(_SIDECAR_SUFFIX))


def build_archive(
    archive_path: Path,
    sources: Sequence[Path],
    titles: Mapping[str, str],
    *,
    content_type: ContentType,
    language: str,
    profile_id: str | None,
    hashtags: Sequence[str] = (),
) -> None:
    """Write the archive next to its final name and atomically replace it.

    The media entry keeps its source extension; ``NNN.json`` names the one
    type of the archive, the address and the video title or the photo text.
    Profile hashtags are written for videos only and only when present. Any
    failure removes the staging file before the exception is re-raised, so a
    failed build never leaves ``.tmp`` next to the archive.
    """
    tmp_path = _tmp_path(archive_path)
    replaced = False
    try:
        tmp_path.unlink(missing_ok=True)
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for index, source in enumerate(sources, start=1):
                prefix = f"{index:03d}"
                archive.write(source, arcname=f"{prefix}{source.suffix}")
                archive.writestr(
                    f"{prefix}{_SIDECAR_SUFFIX}",
                    json.dumps(
                        _entry_sidecar(
                            source,
                            content_type,
                            language,
                            profile_id,
                            titles,
                            hashtags,
                        ),
                        ensure_ascii=False,
                    ),
                )
        tmp_path.replace(archive_path)
        replaced = True
    finally:
        if not replaced:
            discard_tmp(archive_path)


def remove_sources(
    sources: Sequence[Path],
    content_type: ContentType = ContentType.VIDEO,
) -> None:
    """Delete delivered media and photo sidecars; a failed unlink is logged."""
    for source in sources:
        _remove(source)
        if content_type is ContentType.PHOTO:
            _remove(photo_sidecar(source))


def _entry_sidecar(
    source: Path,
    content_type: ContentType,
    language: str,
    profile_id: str | None,
    titles: Mapping[str, str],
    hashtags: Sequence[str],
) -> dict[str, object]:
    """Build the YouTube Kit sidecar of one archive entry."""
    payload: dict[str, object] = {
        "type": content_type.value,
        "language": language,
        "profile_id": profile_id,
    }
    if content_type is ContentType.PHOTO:
        payload["text"] = _photo_text(source)
    else:
        payload["title"] = titles[source.stem]
        if hashtags:
            payload["hashtags"] = list(hashtags)
    return payload


def _photo_text(source: Path) -> str:
    """Return the post text of a photo; an unreadable sidecar means no text."""
    try:
        return photo_text(source)
    except (OSError, ValueError) as ex:
        sidecar = photo_sidecar(source)
        logger.bind(
            path=str(sidecar),
            error=ex,
            error_type=type(ex).__name__,
        ).warning("Sidecar поста не читается, текст выдан пустым: {}", sidecar)
        return ""


def _tmp_path(archive_path: Path) -> Path:
    """Return the staging path of an archive."""
    return archive_path.with_suffix(_TMP_SUFFIX)


def discard_tmp(archive_path: Path) -> None:
    """Remove the staging file after a failed build, logging a failed unlink."""
    try:
        _tmp_path(archive_path).unlink(missing_ok=True)
    except OSError as ex:
        logger.bind(error=ex, error_type=type(ex).__name__).warning(
            "Не удалось удалить временный архив {}",
            archive_path.name,
        )


def _remove(path: Path) -> None:
    """Delete one delivered file, tolerating its absence."""
    try:
        path.unlink(missing_ok=True)
    except OSError as ex:
        logger.bind(path=str(path), error=ex, error_type=type(ex).__name__).warning(
            "Не удалось удалить выданный файл {}",
            path.name,
        )


__all__ = ["build_archive", "count_archive_files", "discard_tmp", "remove_sources"]
