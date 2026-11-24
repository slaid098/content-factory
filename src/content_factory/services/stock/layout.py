"""Stock layout: per-niche language directories and the legacy flat one.

The canonical layout is ``content/<niche>/<language>/<name>.<ext>``. A video
has an ``.mp4``, ``.mov``, ``.webm`` or ``.mkv`` suffix; a photo has an
``.jpg``, ``.jpeg``, ``.png`` or ``.webp`` suffix and may have a
``<name>.json`` sidecar with its post text. Supplier downloads live in the
``general`` niche. A flat ``content/<language>`` directory is still read as
the ``general`` niche so the stock collected before the layout change keeps
working until it is migrated.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping
from pathlib import Path

from loguru import logger

from content_factory.constants import CONTENT_DIR, LANGUAGE_CODE_RE, NICHE_CODE_RE, PROFILES_DIR
from content_factory.schemas.content.delivery import ContentType
from content_factory.schemas.stock.layout import StockFile
from content_factory.utils.filesystem import child_directories

_PROFILE_FILENAME = "profile.yaml"
_SHARED_DIR = "_shared"
_GENERAL = "general"
_SIDECAR_SUFFIX = ".json"
_VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".webm", ".mkv"})
_PHOTO_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})


def stock_files(
    content_dir: Path,
    profiles_root: Path | None = None,
    *,
    content_type: ContentType | None = ContentType.VIDEO,
) -> list[StockFile]:
    """List stock files of the requested content type in a stable order.

    The flat ``content/<language>`` directory is read as the ``general``
    niche. A file that exists in both layouts is returned once, with the
    nested path preferred. Directories deeper than two levels, entries named
    outside the layout and files of unknown suffixes are ignored. A photo
    with a broken text sidecar is not returned at all.

    Args:
        content_dir: Root ``content`` directory of the runtime data.
        profiles_root: Root ``profiles`` directory used to resolve profile
            ids; defaults to the sibling ``profiles`` directory of
            ``content_dir``.
        content_type: Type to list; video by default. ``None`` lists video
            and photo files together.

    Returns:
        Files sorted by ``(language, niche, path.name)``.
    """
    if not content_dir.is_dir():
        return []
    slugs = profile_slugs(profiles_root or content_dir.parent / PROFILES_DIR)
    files: dict[tuple[str, str, str], StockFile] = {}
    for language_dir in _flat_language_dirs(content_dir):
        for path, item_type in _media_files(language_dir, content_type):
            _store(
                files,
                StockFile(
                    _GENERAL,
                    language_dir.name,
                    path,
                    _profile_id(path.name, slugs),
                    item_type,
                ),
            )
    for niche_dir in _niche_dirs(content_dir):
        for language_dir in _nested_language_dirs(niche_dir):
            for path, item_type in _media_files(language_dir, content_type):
                _store(
                    files,
                    StockFile(
                        niche_dir.name,
                        language_dir.name,
                        path,
                        _profile_id(path.name, slugs),
                        item_type,
                    ),
                )
    return sorted(
        files.values(),
        key=lambda item: (item.language, item.niche, item.path.name),
    )


def language_files(
    content_dir: Path,
    language: str,
    *,
    content_type: ContentType | None = ContentType.VIDEO,
) -> list[StockFile]:
    """Return the stock files of one language across every niche.

    Args:
        content_dir: Root ``content`` directory of the runtime data.
        language: Validated two- or three-letter language code.
        content_type: Type to list; video by default. ``None`` lists video
            and photo files together.

    Returns:
        Files of the language sorted by niche and file name.
    """
    return [
        item
        for item in stock_files(content_dir, content_type=content_type)
        if item.language == language
    ]


def photo_sidecar(media_path: Path) -> Path:
    """Return the text sidecar path of one photo post."""
    return media_path.with_suffix(_SIDECAR_SUFFIX)


def photo_text(media_path: Path) -> str:
    """Return the post text of a photo, empty when the sidecar is missing.

    An absent sidecar, an empty JSON object and an empty ``text`` all mean a
    valid post without text.

    Args:
        media_path: Photo file whose sidecar sits next to it.

    Returns:
        The stored text, unchanged.

    Raises:
        ValueError: The sidecar is not a JSON object or ``text`` is not a
            string.
        OSError: An existing sidecar cannot be read.
    """
    sidecar = photo_sidecar(media_path)
    try:
        raw = sidecar.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as ex:
        raise ValueError("некорректный JSON поста") from ex
    if not isinstance(data, dict):
        raise ValueError("sidecar поста должен быть JSON-объектом")
    text = data.get("text", "")
    if not isinstance(text, str):
        raise ValueError("text поста должен быть строкой")
    return text


def migrate_flat_layout(data_dir: Path) -> int:
    """Move flat ``content/<language>`` video files into ``content/general``.

    The move never overwrites an existing file: a name conflict leaves the
    flat file in place and logs both paths. A repeated call finds no flat
    video files and returns zero.

    Args:
        data_dir: Root ``app_data`` directory.

    Returns:
        Number of moved files.
    """
    content_dir = data_dir / CONTENT_DIR
    if not content_dir.is_dir():
        return 0
    moved = 0
    for language_dir in _flat_language_dirs(content_dir):
        destination_dir = content_dir / _GENERAL / language_dir.name
        for path, _item_type in _media_files(language_dir, ContentType.VIDEO):
            destination = destination_dir / path.name
            if destination.exists():
                _warn_conflict(path, destination)
                continue
            try:
                destination_dir.mkdir(parents=True, exist_ok=True)
            except OSError as ex:
                logger.bind(
                    path=str(destination_dir),
                    error=ex,
                    error_type=type(ex).__name__,
                ).warning("Не удалось создать каталог стока {}", destination_dir)
                continue
            if _move_without_overwrite(path, destination):
                moved += 1
    return moved


def profile_slugs(profiles_root: Path) -> dict[str, str]:
    """Map generated file prefixes to profile ids.

    Every ``profiles/<category>/<name>/profile.yaml`` becomes
    ``"<category>_<name>" -> "<category>/<name>"``. The shared ``_shared``
    directory is skipped. A slug claimed by two profiles is ambiguous: it is
    logged and both profiles are left out of the map, so their files get no
    profile id. The same profile name in two categories is not a conflict:
    the category makes their slugs different.

    Args:
        profiles_root: Root ``profiles`` directory of the runtime data.

    Returns:
        Unique ``slug -> profile_id`` pairs.
    """
    if not profiles_root.is_dir():
        return {}
    entries: list[tuple[str, str]] = []
    for category in child_directories(profiles_root):
        if category.name == _SHARED_DIR:
            continue
        for directory in child_directories(category):
            if not (directory / _PROFILE_FILENAME).is_file():
                continue
            entries.append(
                (f"{category.name}_{directory.name}", f"{category.name}/{directory.name}")
            )
    conflicts = _conflicting_profile_ids(entries)
    slugs: dict[str, str] = {}
    for slug, profile_id in sorted(entries):
        if profile_id not in conflicts:
            slugs[slug] = profile_id
    return slugs


def _conflicting_profile_ids(entries: list[tuple[str, str]]) -> set[str]:
    """Return profile ids whose slug is claimed by another profile."""
    conflicts: set[str] = set()
    groups: dict[str, list[str]] = {}
    for slug, profile_id in entries:
        groups.setdefault(slug, []).append(profile_id)
    for slug, profile_ids in sorted(groups.items()):
        if len(profile_ids) < 2:
            continue
        conflicts.update(profile_ids)
        logger.bind(value=slug, profiles=profile_ids).warning(
            "Префикс профиля {} неоднозначен: {}",
            slug,
            ", ".join(profile_ids),
        )
    return conflicts


def _flat_language_dirs(content_dir: Path) -> list[Path]:
    """Return valid flat language directories in name order."""
    directories: list[Path] = []
    for directory in child_directories(content_dir):
        if LANGUAGE_CODE_RE.fullmatch(directory.name) is not None:
            directories.append(directory)
        elif NICHE_CODE_RE.fullmatch(directory.name) is None:
            _warn_invalid(directory, "ниши или языка")
    return directories


def _niche_dirs(content_dir: Path) -> list[Path]:
    """Return niche directories of the nested layout in name order."""
    directories: list[Path] = []
    for directory in child_directories(content_dir):
        if LANGUAGE_CODE_RE.fullmatch(directory.name) is not None:
            continue
        if NICHE_CODE_RE.fullmatch(directory.name) is None:
            continue
        directories.append(directory)
    return directories


def _nested_language_dirs(niche_dir: Path) -> list[Path]:
    """Return valid language directories inside one niche."""
    directories: list[Path] = []
    for directory in child_directories(niche_dir):
        if LANGUAGE_CODE_RE.fullmatch(directory.name) is not None:
            directories.append(directory)
        elif NICHE_CODE_RE.fullmatch(directory.name) is None:
            _warn_invalid(directory, "языка")
    return directories


def _media_files(
    directory: Path,
    content_type: ContentType | None,
) -> list[tuple[Path, ContentType]]:
    """Return regular media files of one directory in name order.

    Files of an unknown suffix are skipped. A photo is returned only when its
    optional text sidecar is readable: a broken sidecar leaves the row out of
    the stock with a warning that carries the path and no file content.
    """
    files: list[tuple[Path, ContentType]] = []
    for path in _entries(directory):
        item_type = _content_type(path)
        if item_type is None or (content_type is not None and item_type is not content_type):
            continue
        if not _is_regular(path):
            continue
        if item_type is ContentType.PHOTO and not _sidecar_is_readable(path):
            continue
        files.append((path, item_type))
    return files


def _entries(directory: Path) -> list[Path]:
    """Return directory entries in name order, or none when it is unreadable."""
    try:
        return sorted(directory.iterdir(), key=lambda path: path.name)
    except OSError as ex:
        logger.bind(path=str(directory), error=ex, error_type=type(ex).__name__).warning(
            "Не удалось прочитать каталог стока {}", directory
        )
        return []


def _content_type(path: Path) -> ContentType | None:
    """Return the stock type of a file by its suffix, or none for other files."""
    suffix = path.suffix.lower()
    if suffix in _VIDEO_SUFFIXES:
        return ContentType.VIDEO
    if suffix in _PHOTO_SUFFIXES:
        return ContentType.PHOTO
    return None


def _is_regular(path: Path) -> bool:
    """Return whether a path is a regular file, logging an unreadable entry."""
    try:
        return path.is_file()
    except OSError as ex:
        logger.bind(path=str(path), error=ex, error_type=type(ex).__name__).debug(
            "Не удалось определить тип файла {}",
            path,
        )
        return True


def _sidecar_is_readable(path: Path) -> bool:
    """Return whether the optional photo sidecar can be indexed."""
    try:
        photo_text(path)
    except (OSError, ValueError) as ex:
        sidecar = photo_sidecar(path)
        logger.bind(
            path=str(sidecar),
            error=ex,
            error_type=type(ex).__name__,
        ).warning("Sidecar поста не читается и файл не индексируется: {}", sidecar)
        return False
    return True


def _store(files: dict[tuple[str, str, str], StockFile], item: StockFile) -> None:
    """Keep one file per ``(language, niche, name)``; nested paths win."""
    files[(item.language, item.niche, item.path.name)] = item


def _profile_id(name: str, slugs: Mapping[str, str]) -> str | None:
    """Return the profile id whose slug prefixes the file name."""
    for slug in sorted(slugs, key=len, reverse=True):
        if name.startswith(f"{slug}_"):
            return slugs[slug]
    return None


def _move_without_overwrite(source: Path, destination: Path) -> bool:
    """Move one file without ever replacing the destination."""
    try:
        os.link(source, destination)
    except FileExistsError:
        _warn_conflict(source, destination)
        return False
    except OSError:
        try:
            _copy_exclusive(source, destination)
        except FileExistsError:
            _warn_conflict(source, destination)
            return False
        except OSError as ex:
            logger.bind(
                source=str(source),
                destination=str(destination),
                error=ex,
                error_type=type(ex).__name__,
            ).warning("Не удалось перенести файл стока {} -> {}", source, destination)
            return False
    try:
        source.unlink()
    except OSError as ex:
        logger.bind(
            source=str(source),
            destination=str(destination),
            error=ex,
            error_type=type(ex).__name__,
        ).warning("Файл стока скопирован, но не удалён из плоской раскладки: {}", source)
        return False
    return True


def _copy_exclusive(source: Path, destination: Path) -> None:
    """Copy a file, failing when the destination already exists."""
    with destination.open("xb") as writer, source.open("rb") as reader:
        shutil.copyfileobj(reader, writer)


def _warn_conflict(source: Path, destination: Path) -> None:
    """Log a name conflict without touching either file."""
    logger.bind(source=str(source), destination=str(destination)).warning(
        "Файл стока уже есть в новой раскладке, оставлен на месте: {} -> {}",
        source,
        destination,
    )


def _warn_invalid(directory: Path, expected: str) -> None:
    """Log a directory name that cannot be a niche or language."""
    logger.bind(path=str(directory), name=directory.name).warning(
        "Каталог {} не подходит как имя {} и пропущен",
        directory,
        expected,
    )


__all__ = [
    "language_files",
    "migrate_flat_layout",
    "photo_sidecar",
    "photo_text",
    "profile_slugs",
    "stock_files",
]
