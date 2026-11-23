"""Runtime settings read, preview and apply service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml.comments import CommentedMap

from content_factory.config.runtime import load_runtime_config, validate_runtime_config
from content_factory.constants import DB_DIR
from content_factory.db.lock import SETTINGS_LOCK, lock_path, locked
from content_factory.exceptions.errors import (
    ConfigError,
    LockError,
    SettingsValidationError,
    SettingsWriteError,
)
from content_factory.schemas.config.runtime import RuntimeConfig
from content_factory.schemas.settings.api import (
    RuntimeSettings,
    SettingsApplyResponse,
    SettingsPreviewResponse,
)
from content_factory.services.settings.diff import unified_diff
from content_factory.services.settings.document import (
    dump_document,
    load_document,
    read_bytes,
    settings_path,
    write_with_backup,
)
from content_factory.services.settings.slice import (
    apply_payload,
    editable_keys_missing,
    settings_from_runtime,
)


@dataclass(frozen=True, slots=True)
class _State:
    """A validated config file snapshot taken under the write lock."""

    runtime: RuntimeConfig
    document: CommentedMap
    text: str
    raw: bytes


def read_settings(data_dir: Path) -> RuntimeSettings:
    """Return the editable slice, seeding the template when the file is absent."""
    try:
        with locked(lock_path(data_dir / DB_DIR, SETTINGS_LOCK)):
            state = _load_state(settings_path(data_dir))
            return settings_from_runtime(state.runtime, state.document)
    except LockError as ex:
        raise ConfigError(f"не удалось заблокировать файл настроек: {ex}") from ex


def preview_settings(data_dir: Path, payload: RuntimeSettings) -> SettingsPreviewResponse:
    """Validate *payload* and return the diff without writing the file."""
    path = settings_path(data_dir)
    try:
        with locked(lock_path(data_dir / DB_DIR, SETTINGS_LOCK)):
            state = _load_validated_state(path)
            current = settings_from_runtime(state.runtime, state.document)
            if not _changed(payload, current, state.document):
                return SettingsPreviewResponse(changed=False, diff="", settings=current)
            text = _render(state, payload)
            if text == state.text:
                return SettingsPreviewResponse(changed=False, diff="", settings=current)
            return SettingsPreviewResponse(
                changed=True,
                diff=unified_diff(state.text, text),
                settings=payload,
            )
    except LockError as ex:
        raise SettingsWriteError(f"не удалось заблокировать файл настроек: {ex}") from ex


def apply_settings(data_dir: Path, payload: RuntimeSettings) -> SettingsApplyResponse:
    """Validate *payload*, back up the file and replace it atomically."""
    path = settings_path(data_dir)
    try:
        with locked(lock_path(data_dir / DB_DIR, SETTINGS_LOCK)):
            state = _load_validated_state(path)
            current = settings_from_runtime(state.runtime, state.document)
            if not _changed(payload, current, state.document):
                return SettingsApplyResponse(
                    changed=False,
                    diff="",
                    backup=None,
                    settings=current,
                )
            text = _render(state, payload)
            if text == state.text:
                return SettingsApplyResponse(
                    changed=False,
                    diff="",
                    backup=None,
                    settings=current,
                )
            backup = write_with_backup(path, text, state.raw)
            return SettingsApplyResponse(
                changed=True,
                diff=unified_diff(state.text, text),
                backup=backup,
                settings=payload,
            )
    except LockError as ex:
        raise SettingsWriteError(f"не удалось заблокировать файл настроек: {ex}") from ex


def _load_state(path: Path) -> _State:
    """Read and validate the file, seeding the template when it is missing."""
    load_runtime_config(path, seed=True)
    raw, text = read_bytes(path)
    document = load_document(text, path)
    runtime = validate_runtime_config(document)
    return _State(runtime=runtime, document=document, text=text, raw=raw)


def _load_validated_state(path: Path) -> _State:
    """Treat an unreadable or invalid existing file as a request error."""
    try:
        return _load_state(path)
    except ConfigError as ex:
        raise SettingsValidationError(str(ex)) from ex


def _changed(
    payload: RuntimeSettings,
    current: RuntimeSettings,
    document: CommentedMap,
) -> bool:
    """Return whether the payload differs or fills a missing editable key."""
    return payload != current or editable_keys_missing(document, payload)


def _render(state: _State, payload: RuntimeSettings) -> str:
    """Apply the payload to the snapshot and serialize the new document."""
    apply_payload(state.document, payload, state.runtime)
    try:
        validate_runtime_config(state.document)
    except ConfigError as ex:
        raise SettingsValidationError(str(ex)) from ex
    return dump_document(state.document)
