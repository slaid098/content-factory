"""Round-trip YAML helpers for the runtime settings file."""

from __future__ import annotations

import io
import os
import stat
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError

from content_factory.constants import CONFIG_DIR, CONFIG_FILENAME
from content_factory.exceptions.errors import ConfigError, SettingsWriteError


def settings_path(data_dir: Path) -> Path:
    """Return the runtime settings file path under *data_dir*."""
    return data_dir / CONFIG_DIR / CONFIG_FILENAME


def read_bytes(path: Path) -> tuple[bytes, str]:
    """Read the config file bytes and decode them as UTF-8.

    Raises:
        ConfigError: The file cannot be read or is not valid UTF-8.
    """
    try:
        data = path.read_bytes()
    except OSError as ex:
        raise ConfigError(f"не удалось прочитать файл конфигурации {path}: {ex}") from ex
    try:
        return data, data.decode("utf-8")
    except UnicodeError as ex:
        raise ConfigError(f"не удалось прочитать файл конфигурации {path}: {ex}") from ex


def load_document(text: str, path: Path) -> CommentedMap:
    """Parse a round-trip YAML document, keeping comments and key order.

    Raises:
        ConfigError: The YAML is broken or the top level is not a mapping.
    """
    if not text.strip():
        return CommentedMap()
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    try:
        data = yaml.load(text)
    except YAMLError as ex:
        raise ConfigError(f"не удалось разобрать YAML {path}: {ex}") from ex
    if data is None:
        return CommentedMap()
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: ожидался словарь верхнего уровня")
    return cast(CommentedMap, data)


def dump_document(document: Mapping[str, Any]) -> str:
    """Serialize a document without dropping comments or quote style."""
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096
    stream = io.StringIO()
    try:
        yaml.dump(document, stream)
    except YAMLError as ex:
        raise SettingsWriteError(f"не удалось сериализовать конфигурацию: {ex}") from ex
    return stream.getvalue()


def write_with_backup(path: Path, text: str, old_bytes: bytes) -> str:
    """Replace *path* atomically after saving a timestamped backup.

    The temporary file is created in the same directory, so ``os.replace``
    stays atomic on one filesystem. A failed write leaves the old file intact
    and removes both the temporary file and the just-created backup.

    Returns:
        The backup file name, for example ``config.yaml.bak-20260501-120000``.

    Raises:
        SettingsWriteError: The backup or the new file cannot be written.
    """
    mode = _file_mode(path)
    backup = _create_backup(path, old_bytes, mode)
    try:
        temporary = _write_temporary(path, text, mode)
    except SettingsWriteError:
        _remove(backup)
        raise
    try:
        Path(temporary).replace(path)
    except OSError as ex:
        _remove(temporary)
        _remove(backup)
        raise SettingsWriteError(f"не удалось записать {path}: {ex}") from ex
    return backup.name


def _file_mode(path: Path) -> int | None:
    """Return the permission bits of the existing file, if any."""
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return None


def _create_backup(path: Path, data: bytes, mode: int | None) -> Path:
    """Create a new backup file and never overwrite an existing one."""
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = f"{path.name}.bak-{stamp}"
    for attempt in range(1000):
        name = base if attempt == 0 else f"{base}-{attempt}"
        candidate = path.with_name(name)
        try:
            with candidate.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if mode is not None:
                candidate.chmod(mode)
        except FileExistsError:
            continue
        except OSError as ex:
            _remove(candidate)
            raise SettingsWriteError(f"не удалось создать бэкап {candidate}: {ex}") from ex
        return candidate
    raise SettingsWriteError(f"не удалось создать бэкап {path}: слишком много файлов")


def _write_temporary(path: Path, text: str, mode: int | None) -> Path:
    """Write *text* to a temporary file in the target directory."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
    except OSError as ex:
        raise SettingsWriteError(f"не удалось записать {path}: {ex}") from ex
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as writer:
            writer.write(text)
            writer.flush()
            os.fsync(writer.fileno())
        if mode is not None:
            Path(temporary).chmod(mode)
    except OSError as ex:
        _remove(Path(temporary))
        raise SettingsWriteError(f"не удалось записать {path}: {ex}") from ex
    return Path(temporary)


def _remove(path: Path) -> None:
    """Remove a file if it still exists."""
    with suppress(OSError):
        path.unlink()
