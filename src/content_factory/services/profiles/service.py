"""Profile API views, refill settings and preview orchestration."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from loguru import logger

from content_factory.constants import DB_DIR, DB_FILENAME, PROFILES_DIR
from content_factory.db.connection import connect
from content_factory.db.errors import DatabaseError
from content_factory.db.profiles import get_refill, upsert_refill
from content_factory.exceptions.errors import (
    ProfileStorageError,
    ProfileValidationError,
)
from content_factory.schemas.config.runtime import GeneratorConfig
from content_factory.schemas.profiles.api import (
    ProductionCheck,
    ProductionStep,
    ProfileDeliverySettings,
    ProfileDetailResponse,
    ProfileOverlayStyle,
    ProfileRentSettings,
    ProfileResponse,
    ProfileSettings,
    ProfileTextSettings,
    ProfileUpdateRequest,
    ProfileVideoSettings,
)
from content_factory.schemas.profiles.preview import ProfilePreview
from content_factory.schemas.profiles.refill import ProfileRefill
from content_factory.schemas.profiles.registry import ProfileInfo
from content_factory.services import production
from content_factory.services.generator import load_profile
from content_factory.services.profiles.preview import profile_preview
from content_factory.services.profiles.prompts import (
    read_profile_prompts,
    write_card_prompt,
    write_prompt_template,
    write_text_prompt,
)
from content_factory.services.profiles.refill import decide_refill
from content_factory.services.profiles.registry import (
    get_profile,
    is_safe_profile_id,
    list_profiles,
)
from content_factory.services.profiles.views import _DISABLED, _counters, _view

_TARGET_MIN = 1
_TARGET_MAX = 1000
_PHOTO_REFILL_MAX = 999


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
    stock, photo_stock, refills = _counters(data_dir, (profile,))
    return _view(
        profile,
        stock.get(profile.id, 0),
        photo_stock.get(profile.id, 0),
        refills.get(profile.id, _DISABLED),
        output_dir,
        global_config,
    )


def get_profile_detail(
    data_dir: Path,
    profile_id: str,
    *,
    output_dir: Path,
    global_config: GeneratorConfig,
) -> ProfileDetailResponse:
    """Return one profile with its stock, recipe and safe settings.

    Args:
        data_dir: Root of the runtime data directory, usually ``app_data``.
        profile_id: Profile identifier such as ``dating/profile_2``.
        output_dir: ``generator.output_dir`` of the generator settings.
        global_config: Global target and refill threshold.

    Returns:
        The profile view with every production step and settings group.

    Raises:
        ProfileError: The id is unsafe, the profile is missing or malformed.
    """
    info = get_profile(data_dir, profile_id)
    stock, photo_stock, refills = _counters(data_dir, (info,))
    view = _view(
        info,
        stock.get(info.id, 0),
        photo_stock.get(info.id, 0),
        refills.get(info.id, _DISABLED),
        output_dir,
        global_config,
    )
    loaded = load_profile(profile_id, data_dir / PROFILES_DIR)
    return ProfileDetailResponse(
        **view.model_dump(),
        production=_production(loaded),
        settings=_settings(loaded, info),
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
    ``0 <= refill_at < target`` and ``1 <= target <= 1000``. The photo pair
    has no fallback: ``0 <= photo_target <= 1000``, ``0 <= photo_refill_at
    <= 999``, and either both are zero or ``photo_refill_at < photo_target``.

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
    stock, photo_stock, refills = _counters(data_dir, (profile,))
    return _view(
        profile,
        stock.get(profile.id, 0),
        photo_stock.get(profile.id, 0),
        refills.get(profile.id, updated),
        output_dir,
        global_config,
    )


def preview_for_profile(data_dir: Path, profile_id: str) -> ProfilePreview | None:
    """Return the preview of one profile: a video frame or its newest photo.

    Args:
        data_dir: Root of the runtime data directory, usually ``app_data``.
        profile_id: Profile identifier such as ``dating/profile_2``.

    Returns:
        The preview file with its media type, or ``None`` when the profile has
        neither a video nor a photo.

    Raises:
        ProfileError: The id is unsafe, the profile is missing or malformed.
        ProfilePreviewError: ffprobe or ffmpeg cannot build the video preview.
    """
    return profile_preview(data_dir, get_profile(data_dir, profile_id))


def _production(profile: Any) -> tuple[ProductionStep, ...]:
    """Build the recipe view in declaration order with resolved QA stages."""
    return tuple(
        ProductionStep(
            producer=layer.producer,
            type=cast(Literal["video", "photo"], layer.output.value),
            checks=tuple(
                ProductionCheck(
                    name=name,
                    stage=cast(
                        Literal["source", "final"],
                        production.qa.resolve(name).stage,
                    ),
                )
                for name in layer.checks
            ),
        )
        for layer in profile.production
    )


def _settings(profile: Any, info: ProfileInfo) -> ProfileSettings:
    """Build the safe read-only settings groups of one profile."""
    title = info.delivery.title
    return ProfileSettings(
        video=ProfileVideoSettings(
            width=profile.width,
            height=profile.height,
            frames=profile.frames,
            frames_jitter=profile.frames_jitter,
            fps=profile.fps,
            renderer=profile.renderer,
            image=profile.image,
            audio_tracks=len(profile.audio_tracks),
        ),
        rent=ProfileRentSettings(
            gpu=profile.gpu,
            max_cost=profile.max_cost,
            disk_space=profile.rent.disk_space,
            gpu_ram=profile.rent.gpu_ram,
            inet_down=profile.rent.inet_down,
            inet_up_cost=profile.rent.inet_up_cost,
            inet_down_cost=profile.rent.inet_down_cost,
        ),
        text=ProfileTextSettings(
            font=profile.font.name,
            negative_prompt=profile.negative_prompt,
            cfg=profile.cfg,
            style=ProfileOverlayStyle(**asdict(profile.style)),
        ),
        delivery=ProfileDeliverySettings(
            title_mode=title.mode.value,
            title=title.value,
            hashtags=info.delivery.hashtags,
        ),
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
    photo_target = payload.photo_target
    if "photo_target" not in fields or photo_target is None:
        photo_target = current.photo_target
    photo_refill_at = payload.photo_refill_at
    if "photo_refill_at" not in fields or photo_refill_at is None:
        photo_refill_at = current.photo_refill_at
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
    _validate_photo(photo_target, photo_refill_at)
    return ProfileRefill(
        enabled=bool(enabled),
        target=target,
        refill_at=refill_at,
        photo_target=photo_target,
        photo_refill_at=photo_refill_at,
    )


def _validate_photo(target: int, refill_at: int) -> None:
    """Validate the photo goal and threshold pair.

    Raises:
        ProfileValidationError: A value is out of range, a zero goal has a
            non-zero threshold, or a non-zero threshold is not below the goal.
    """
    if not 0 <= target <= _TARGET_MAX:
        raise ProfileValidationError(
            f"photo_target должен быть в диапазоне 0..{_TARGET_MAX}: {target}"
        )
    if not 0 <= refill_at <= _PHOTO_REFILL_MAX:
        raise ProfileValidationError(
            f"photo_refill_at должен быть в диапазоне 0..{_PHOTO_REFILL_MAX}: {refill_at}"
        )
    if target == 0:
        if refill_at != 0:
            raise ProfileValidationError(
                f"при photo_target=0 photo_refill_at должен быть 0: {refill_at}"
            )
        return
    if refill_at >= target:
        raise ProfileValidationError(
            f"photo_refill_at должен быть меньше photo_target: {refill_at} >= {target}"
        )


def _read_refill(data_dir: Path, profile_id: str) -> ProfileRefill:
    """Read the stored settings of one profile."""
    try:
        connection = connect(data_dir / DB_DIR / DB_FILENAME)
    except (OSError, DatabaseError) as ex:
        _storage_failure(profile_id, ex, "прочитать")
        raise ProfileStorageError("не удалось прочитать настройки профиля") from ex
    try:
        return get_refill(connection, profile_id)
    except DatabaseError as ex:
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
    except (OSError, DatabaseError) as ex:
        _storage_failure(profile_id, ex, "сохранить")
        raise ProfileStorageError("не удалось сохранить настройки профиля") from ex
    try:
        upsert_refill(connection, profile_id, refill, moment.isoformat())
        connection.commit()
    except DatabaseError as ex:
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


__all__ = [
    "decide_refill",
    "get_profile",
    "get_profile_detail",
    "get_profile_view",
    "is_safe_profile_id",
    "list_profiles",
    "preview_for_profile",
    "read_profile_prompts",
    "update_profile_settings",
    "write_card_prompt",
    "write_prompt_template",
    "write_text_prompt",
]
