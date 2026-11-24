"""Profile API views, refill settings and preview orchestration."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from content_factory.constants import DB_DIR, DB_FILENAME
from content_factory.db.connection import connect
from content_factory.db.profiles import ensure_schema as ensure_settings_schema
from content_factory.db.profiles import get_refill, upsert_refill
from content_factory.db.stock import count_by_profile
from content_factory.exceptions.errors import (
    ProfileStorageError,
    ProfileValidationError,
)
from content_factory.schemas.config.runtime import GeneratorConfig
from content_factory.schemas.profiles.api import ProfileResponse, ProfileUpdateRequest
from content_factory.schemas.profiles.refill import ProfileRefill
from content_factory.schemas.profiles.registry import ProfileInfo
from content_factory.services.profiles.preview import profile_preview
from content_factory.services.profiles.refill import decide_refill
from content_factory.services.profiles.registry import (
    get_profile,
    is_safe_profile_id,
    list_profiles,
)
from content_factory.services.profiles.reports import latest_run
from content_factory.services.stock import sync_stock

_DISABLED = ProfileRefill(False, None, None)
_TARGET_MIN = 1
_TARGET_MAX = 1000


def list_profile_views(
    data_dir: Path,
    *,
    output_dir: Path,
    global_config: GeneratorConfig,
) -> tuple[ProfileResponse, ...]:
    """Return every registry profile with stock and refill settings.

    Args:
        data_dir: Root of the runtime data directory, usually ``app_data``.
        output_dir: ``generator.output_dir`` of the generator settings.
        global_config: Global target and refill threshold.

    Returns:
        Profiles in id order; an empty tuple when the registry is empty.
    """
    profiles = list_profiles(data_dir)
    stock, refills = _counters(data_dir, profiles)
    return tuple(
        _view(
            profile,
            stock.get(profile.id, 0),
            refills.get(profile.id, _DISABLED),
            output_dir,
            global_config,
        )
        for profile in profiles
    )


def get_profile_view(
    data_dir: Path,
    profile_id: str,
    *,
    output_dir: Path,
    global_config: GeneratorConfig,
) -> ProfileResponse:
    """Return one profile with stock and refill settings.

    Args:
        data_dir: Root of the runtime data directory, usually ``app_data``.
        profile_id: Profile identifier such as ``dating/profile_2``.
        output_dir: ``generator.output_dir`` of the generator settings.
        global_config: Global target and refill threshold.

    Raises:
        ProfileError: The id is unsafe, the profile is missing or malformed.
    """
    profile = get_profile(data_dir, profile_id)
    stock, refills = _counters(data_dir, (profile,))
    return _view(
        profile,
        stock.get(profile.id, 0),
        refills.get(profile.id, _DISABLED),
        output_dir,
        global_config,
    )


def update_profile_settings(
    data_dir: Path,
    profile_id: str,
    payload: ProfileUpdateRequest,
    *,
    output_dir: Path,
    global_config: GeneratorConfig,
    now: datetime | None = None,
) -> ProfileResponse:
    """Apply a partial update of the per-profile refill settings.

    A missing field keeps its value and an explicit ``null`` clears the
    override. The resulting effective pair must satisfy
    ``0 <= refill_at < target`` and ``1 <= target <= 1000``.

    Args:
        data_dir: Root of the runtime data directory, usually ``app_data``.
        profile_id: Profile identifier such as ``dating/profile_2``.
        payload: Fields to change; absent fields stay untouched.
        output_dir: ``generator.output_dir`` of the generator settings.
        global_config: Global target and refill threshold.
        now: Moment of the change; defaults to UTC now.

    Returns:
        The profile with the stored settings and its current stock.

    Raises:
        ProfileError: The id is unsafe, the profile is missing or malformed.
        ProfileValidationError: The effective pair is out of range.
        ProfileStorageError: The settings cannot be read or written.
    """
    profile = get_profile(data_dir, profile_id)
    moment = now or datetime.now(UTC)
    current = _read_refill(data_dir, profile.id)
    updated = _updated_refill(payload, current, global_config)
    _write_refill(data_dir, profile.id, updated, moment)
    stock, refills = _counters(data_dir, (profile,))
    return _view(
        profile,
        stock.get(profile.id, 0),
        refills.get(profile.id, updated),
        output_dir,
        global_config,
    )


def preview_for_profile(data_dir: Path, profile_id: str) -> Path | None:
    """Return the cached JPEG preview of one profile.

    Args:
        data_dir: Root of the runtime data directory, usually ``app_data``.
        profile_id: Profile identifier such as ``dating/profile_2``.

    Returns:
        Path to the preview, or ``None`` when the profile has no mp4 yet.

    Raises:
        ProfileError: The id is unsafe, the profile is missing or malformed.
        ProfilePreviewError: ffprobe or ffmpeg cannot build the preview.
    """
    return profile_preview(data_dir, get_profile(data_dir, profile_id))


def _view(
    profile: ProfileInfo,
    stock: int,
    refill: ProfileRefill,
    output_dir: Path,
    global_config: GeneratorConfig,
) -> ProfileResponse:
    """Build the API response of one profile."""
    return ProfileResponse(
        id=profile.id,
        category=profile.category,
        name=profile.name,
        language=profile.language,
        niche=profile.niche,
        width=profile.width,
        height=profile.height,
        audio=profile.audio,
        stock=stock,
        target=refill.target if refill.target is not None else global_config.target,
        refill_at=(refill.refill_at if refill.refill_at is not None else global_config.refill_at),
        refill_enabled=refill.enabled,
        target_override=refill.target,
        refill_at_override=refill.refill_at,
        preview_url=f"/api/v1/content/profiles/{profile.category}/{profile.name}/preview",
        last_run=latest_run(output_dir, profile),
    )


def _updated_refill(
    payload: ProfileUpdateRequest,
    current: ProfileRefill,
    global_config: GeneratorConfig,
) -> ProfileRefill:
    """Merge a partial request with the stored settings and validate it."""
    fields = payload.model_fields_set
    enabled = payload.refill_enabled if "refill_enabled" in fields else current.enabled
    target = payload.target if "target" in fields else current.target
    refill_at = payload.refill_at if "refill_at" in fields else current.refill_at
    effective_target = target if target is not None else global_config.target
    effective_refill_at = refill_at if refill_at is not None else global_config.refill_at
    if not _TARGET_MIN <= effective_target <= _TARGET_MAX:
        raise ProfileValidationError(
            f"target должен быть в диапазоне {_TARGET_MIN}..{_TARGET_MAX}: {effective_target}"
        )
    if effective_refill_at < 0 or effective_refill_at >= effective_target:
        raise ProfileValidationError(
            f"refill_at должен быть меньше target: {effective_refill_at} >= {effective_target}"
        )
    return ProfileRefill(
        enabled=bool(enabled),
        target=target,
        refill_at=refill_at,
    )


def _read_refill(data_dir: Path, profile_id: str) -> ProfileRefill:
    """Read the stored settings of one profile."""
    try:
        connection = connect(data_dir / DB_DIR / DB_FILENAME)
    except (OSError, sqlite3.Error) as ex:
        _storage_failure(profile_id, ex, "прочитать")
        raise ProfileStorageError("не удалось прочитать настройки профиля") from ex
    try:
        ensure_settings_schema(connection)
        return get_refill(connection, profile_id)
    except sqlite3.Error as ex:
        _storage_failure(profile_id, ex, "прочитать")
        raise ProfileStorageError("не удалось прочитать настройки профиля") from ex
    finally:
        connection.close()


def _write_refill(
    data_dir: Path,
    profile_id: str,
    refill: ProfileRefill,
    moment: datetime,
) -> None:
    """Persist the settings of one profile."""
    try:
        connection = connect(data_dir / DB_DIR / DB_FILENAME)
    except (OSError, sqlite3.Error) as ex:
        _storage_failure(profile_id, ex, "сохранить")
        raise ProfileStorageError("не удалось сохранить настройки профиля") from ex
    try:
        ensure_settings_schema(connection)
        upsert_refill(connection, profile_id, refill, moment.isoformat())
        connection.commit()
    except sqlite3.Error as ex:
        _storage_failure(profile_id, ex, "сохранить")
        raise ProfileStorageError("не удалось сохранить настройки профиля") from ex
    finally:
        connection.close()


def _storage_failure(profile_id: str, ex: BaseException, action: str) -> None:
    """Log a failed settings access without the stored values."""
    logger.bind(
        profile_id=profile_id,
        error=ex,
        error_type=type(ex).__name__,
    ).error("Не удалось {} настройки профиля {}", action, profile_id)


def _counters(
    data_dir: Path,
    profiles: tuple[ProfileInfo, ...],
) -> tuple[dict[str, int], dict[str, ProfileRefill]]:
    """Synchronize the stock index and read counts with settings.

    A database failure is logged and yields zeros with disabled settings:
    the list of profiles stays available even when accounting is broken.
    """
    try:
        sync_stock(data_dir)
    except (OSError, sqlite3.Error) as ex:
        logger.bind(
            path=str(data_dir / DB_DIR / DB_FILENAME),
            error=ex,
            error_type=type(ex).__name__,
        ).warning("Не удалось синхронизировать индекс стока")
        return {}, {}
    try:
        connection = connect(data_dir / DB_DIR / DB_FILENAME)
    except (OSError, sqlite3.Error) as ex:
        logger.bind(
            path=str(data_dir / DB_DIR / DB_FILENAME),
            error=ex,
            error_type=type(ex).__name__,
        ).warning("Не удалось прочитать настройки профилей")
        return {}, {}
    try:
        ensure_settings_schema(connection)
        stock = {profile.id: count_by_profile(connection, profile.id) for profile in profiles}
        refills = {profile.id: get_refill(connection, profile.id) for profile in profiles}
    except sqlite3.Error as ex:
        logger.bind(
            path=str(data_dir / DB_DIR / DB_FILENAME),
            error=ex,
            error_type=type(ex).__name__,
        ).warning("Не удалось прочитать настройки профилей")
        return {}, {}
    finally:
        connection.close()
    return stock, refills


__all__ = [
    "decide_refill",
    "get_profile",
    "get_profile_view",
    "is_safe_profile_id",
    "list_profile_views",
    "list_profiles",
    "preview_for_profile",
    "update_profile_settings",
]
