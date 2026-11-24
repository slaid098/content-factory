"""Stock layout: per-niche language directories and the legacy flat one.

The canonical layout is ``content/<niche>/<language>/<name>.mp4``. Supplier
downloads live in the ``general`` niche. A flat ``content/<language>``
directory is still read as the ``general`` niche so the stock collected
before the layout change keeps working until it is migrated.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from pathlib import Path

from loguru import logger

from content_factory.constants import CONTENT_DIR, LANGUAGE_CODE_RE, NICHE_CODE_RE, PROFILES_DIR
from content_factory.schemas.stock.layout import StockFile

_PROFILE_FILENAME = "profile.yaml"
_SHARED_DIR = "_shared"
_GENERAL = "general"
_MP4_SUFFIX = ".mp4"


def stock_files(
    content_dir: Path,
    profiles_root: Path | None = None,
) -> list[StockFile]:
    """List every stock mp4 of both layouts in a stable order.

    The flat ``content/<language>`` directory is read as the ``general``
    niche. A file that exists in both layouts is returned once, with the
    nested path preferred. Directories deeper than two levels and entries
    named outside the layout are ignored.

    Args:
        content_dir: Root ``content`` directory of the runtime data.
        profiles_root: Root ``profiles`` directory used to resolve profile
            ids; defaults to the sibling ``profiles`` directory of
            ``content_dir``.

    Returns:
        Files sorted by ``(language, niche, path.name)``.
    """
    if not content_dir.is_dir():
        return []
    slugs = profile_slugs(profiles_root or content_dir.parent / PROFILES_DIR)
    files: dict[tuple[str, str, str], StockFile] = {}
    for language_dir in _flat_language_dirs(content_dir):
        for path in _mp4_files(language_dir):
            _store(
                files,
                StockFile(_GENERAL, language_dir.name, path, _profile_id(path.name, slugs)),
            )
    for niche_dir in _niche_dirs(content_dir):
        for language_dir in _nested_language_dirs(niche_dir):
            for path in _mp4_files(language_dir):
                _store(
                    files,
                    StockFile(
                        niche_dir.name,
                        language_dir.name,
                        path,
                        _profile_id(path.name, slugs),
                    ),
                )
    return sorted(
        files.values(),
        key=lambda item: (item.language, item.niche, item.path.name),
    )


def language_files(content_dir: Path, language: str) -> list[StockFile]:
    """Return the stock files of one language across every niche.

    Args:
        content_dir: Root ``content`` directory of the runtime data.
        language: Validated two- or three-letter language code.

    Returns:
        Files of the language sorted by niche and file name.
    """
    return [item for item in stock_files(content_dir) if item.language == language]


def migrate_flat_layout(data_dir: Path) -> int:
    """Move flat ``content/<language>`` mp4 files into ``content/general``.

    The move never overwrites an existing file: a name conflict leaves the
    flat file in place and logs both paths. A repeated call finds no flat mp4
    files and returns zero.

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
        for path in _mp4_files(language_dir):
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
    directory is skipped. A slug claimed by two profiles, or a profile name
    repeated in two categories, is ambiguous: it is logged and left out of
    the map, so its files get no profile id.

    Args:
        profiles_root: Root ``profiles`` directory of the runtime data.

    Returns:
        Unique ``slug -> profile_id`` pairs.
    """
    if not profiles_root.is_dir():
        return {}
    entries: list[tuple[str, str, str, str]] = []
    for category in _child_dirs(profiles_root):
        if category.name == _SHARED_DIR:
            continue
        for directory in _child_dirs(category):
            if not (directory / _PROFILE_FILENAME).is_file():
                continue
            entries.append(
                (
                    f"{category.name}_{directory.name}",
                    f"{category.name}/{directory.name}",
                    category.name,
                    directory.name,
                )
            )
    conflicts = _conflicting_profile_ids(entries)
    slugs: dict[str, str] = {}
    for slug, profile_id, _, _ in sorted(entries):
        if profile_id not in conflicts:
            slugs[slug] = profile_id
    return slugs


def _conflicting_profile_ids(entries: list[tuple[str, str, str, str]]) -> set[str]:
    """Return profile ids whose slug or bare name is shared with another."""
    conflicts: set[str] = set()
    for field in (0, 3):
        groups: dict[str, list[str]] = {}
        for entry in entries:
            groups.setdefault(entry[field], []).append(entry[1])
        for key, profile_ids in sorted(groups.items()):
            if len(profile_ids) < 2:
                continue
            conflicts.update(profile_ids)
            logger.bind(value=key, profiles=profile_ids).warning(
                "Префикс профиля {} неоднозначен: {}",
                key,
                ", ".join(profile_ids),
            )
    return conflicts


def _flat_language_dirs(content_dir: Path) -> list[Path]:
    """Return valid flat language directories in name order."""
    directories: list[Path] = []
    for directory in _child_dirs(content_dir):
        if LANGUAGE_CODE_RE.fullmatch(directory.name) is not None:
            directories.append(directory)
        elif NICHE_CODE_RE.fullmatch(directory.name) is None:
            _warn_invalid(directory, "ниши или языка")
    return directories


def _niche_dirs(content_dir: Path) -> list[Path]:
    """Return niche directories of the nested layout in name order."""
    directories: list[Path] = []
    for directory in _child_dirs(content_dir):
        if LANGUAGE_CODE_RE.fullmatch(directory.name) is not None:
            continue
        if NICHE_CODE_RE.fullmatch(directory.name) is None:
            continue
        directories.append(directory)
    return directories


def _nested_language_dirs(niche_dir: Path) -> list[Path]:
    """Return valid language directories inside one niche."""
    directories: list[Path] = []
    for directory in _child_dirs(niche_dir):
        if LANGUAGE_CODE_RE.fullmatch(directory.name) is not None:
            directories.append(directory)
        elif NICHE_CODE_RE.fullmatch(directory.name) is None:
            _warn_invalid(directory, "языка")
    return directories


def _child_dirs(root: Path) -> list[Path]:
    """Return child directories in name order, logging a read error."""
    try:
        entries = sorted(root.iterdir(), key=lambda path: path.name)
    except OSError as ex:
        logger.bind(path=str(root), error=ex, error_type=type(ex).__name__).warning(
            "Не удалось прочитать каталог стока {}", root
        )
        return []
    return [entry for entry in entries if entry.is_dir()]


def _mp4_files(directory: Path) -> list[Path]:
    """Return regular mp4 files of one directory in name order."""
    try:
        entries = sorted(directory.iterdir(), key=lambda path: path.name)
    except OSError as ex:
        logger.bind(path=str(directory), error=ex, error_type=type(ex).__name__).warning(
            "Не удалось прочитать каталог стока {}", directory
        )
        return []
    files: list[Path] = []
    for path in entries:
        if path.suffix != _MP4_SUFFIX:
            continue
        try:
            regular = path.is_file()
        except OSError as ex:
            logger.bind(path=str(path), error=ex, error_type=type(ex).__name__).debug(
                "Не удалось определить тип файла {}",
                path,
            )
            regular = True
        if regular:
            files.append(path)
    return files


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
    "profile_slugs",
    "stock_files",
]
