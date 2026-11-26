"""Read-side API views of generator profiles."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

from loguru import logger

from content_factory.constants import (
    DB_DIR,
    DB_FILENAME,
    LANGUAGE_LABELS,
    language_label,
)
from content_factory.db.connection import connect
from content_factory.db.errors import DatabaseError
from content_factory.db.profiles import get_refill
from content_factory.db.stock import count_by_profile
from content_factory.schemas.config.runtime import GeneratorConfig
from content_factory.schemas.content.delivery import ContentType
from content_factory.schemas.profiles.api import (
    LanguageOption,
    ProfileResponse,
    ProfilesResponse,
)
from content_factory.schemas.profiles.refill import ProfileRefill
from content_factory.schemas.profiles.registry import ProfileInfo
from content_factory.services.profiles.registry import list_profiles
from content_factory.services.profiles.reports import latest_run
from content_factory.services.stock import sync_stock

_DISABLED = ProfileRefill(False, None, None)


def list_profile_views(
    data_dir: Path,
    *,
    output_dir: Path,
    global_config: GeneratorConfig,
    language: str | None = None,
    niche: str | None = None,
) -> tuple[ProfileResponse, ...]:
    """Return registry profiles with stock and refill settings.

    Args:
        data_dir: Root of the runtime data directory, usually ``app_data``.
        output_dir: ``generator.output_dir`` of the generator settings.
        global_config: Global target and refill threshold.
        language: Exact language code to keep, or ``None`` for every profile.
        niche: Exact niche to keep, or ``None`` for every profile.

    Returns:
        Matching profiles in id order; an empty tuple when none match.
    """
    return _views(
        data_dir,
        list_profiles(data_dir),
        output_dir=output_dir,
        global_config=global_config,
        language=language,
        niche=niche,
    )


def list_profiles_response(
    data_dir: Path,
    *,
    output_dir: Path,
    global_config: GeneratorConfig,
    language: str | None = None,
    niche: str | None = None,
) -> ProfilesResponse:
    """Return the filtered profile list with its filter catalog.

    ``languages`` and ``niches`` describe every registry profile, while
    ``total`` counts them before filtering, so the client keeps the filter
    options even when the filtered list is empty.

    Args:
        data_dir: Root of the runtime data directory, usually ``app_data``.
        output_dir: ``generator.output_dir`` of the generator settings.
        global_config: Global target and refill threshold.
        language: Exact language code to keep, or ``None`` for every profile.
        niche: Exact niche to keep, or ``None`` for every profile.

    Returns:
        The matching profile views and the unfiltered catalog.
    """
    profiles = list_profiles(data_dir)
    return ProfilesResponse(
        profiles=_views(
            data_dir,
            profiles,
            output_dir=output_dir,
            global_config=global_config,
            language=language,
            niche=niche,
        ),
        languages=_language_options(profiles),
        niches=tuple(sorted({profile.niche for profile in profiles})),
        total=len(profiles),
    )


def _views(
    data_dir: Path,
    profiles: tuple[ProfileInfo, ...],
    *,
    output_dir: Path,
    global_config: GeneratorConfig,
    language: str | None,
    niche: str | None,
) -> tuple[ProfileResponse, ...]:
    """Build views of the profiles that match both filters, in id order."""
    selected = tuple(
        profile
        for profile in profiles
        if (language is None or profile.language == language)
        and (niche is None or profile.niche == niche)
    )
    stock, photo_stock, refills = _counters(data_dir, selected)
    return tuple(
        _view(
            profile,
            stock.get(profile.id, 0),
            photo_stock.get(profile.id, 0),
            refills.get(profile.id, _DISABLED),
            output_dir,
            global_config,
        )
        for profile in selected
    )


def _language_options(
    profiles: tuple[ProfileInfo, ...],
) -> tuple[LanguageOption, ...]:
    """Return the full catalog plus profile codes outside it, by label."""
    labels = dict(LANGUAGE_LABELS)
    for profile in profiles:
        labels.setdefault(profile.language, language_label(profile.language))
    return tuple(
        LanguageOption(code=code, label=label)
        for code, label in sorted(labels.items(), key=lambda item: item[1])
    )


def _view(
    profile: ProfileInfo,
    stock: int,
    photo_stock: int,
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
        language_label=language_label(profile.language),
        niche=profile.niche,
        title=profile.title,
        summary=profile.summary,
        width=profile.width,
        height=profile.height,
        audio=profile.audio,
        outputs=tuple(
            cast(Literal["video", "photo"], content_type.value)
            for content_type in (ContentType.VIDEO, ContentType.PHOTO)
            if content_type in profile.outputs
        ),
        stock=stock,
        photo_stock=photo_stock,
        target=refill.target if refill.target is not None else global_config.target,
        refill_at=(refill.refill_at if refill.refill_at is not None else global_config.refill_at),
        refill_enabled=refill.enabled,
        target_override=refill.target,
        refill_at_override=refill.refill_at,
        photo_target=refill.photo_target,
        photo_refill_at=refill.photo_refill_at,
        preview_url=f"/api/v1/content/profiles/{profile.category}/{profile.name}/preview",
        last_run=latest_run(output_dir, profile),
    )


def _counters(
    data_dir: Path,
    profiles: tuple[ProfileInfo, ...],
) -> tuple[dict[str, int], dict[str, int], dict[str, ProfileRefill]]:
    """Synchronize the stock index and read video, photo and refill values.

    A database failure is logged and yields zeros with disabled settings:
    the list of profiles stays available even when accounting is broken.
    """
    try:
        sync_stock(data_dir)
    except (OSError, DatabaseError) as ex:
        logger.bind(
            path=str(data_dir / DB_DIR / DB_FILENAME),
            error=ex,
            error_type=type(ex).__name__,
        ).warning("Не удалось синхронизировать индекс стока")
        return {}, {}, {}
    try:
        connection = connect(data_dir / DB_DIR / DB_FILENAME)
    except (OSError, DatabaseError) as ex:
        logger.bind(
            path=str(data_dir / DB_DIR / DB_FILENAME),
            error=ex,
            error_type=type(ex).__name__,
        ).warning("Не удалось прочитать настройки профилей")
        return {}, {}, {}
    try:
        stock = {profile.id: count_by_profile(connection, profile.id) for profile in profiles}
        photo_stock = {
            profile.id: count_by_profile(connection, profile.id, content_type=ContentType.PHOTO)
            for profile in profiles
        }
        refills = {profile.id: get_refill(connection, profile.id) for profile in profiles}
    except DatabaseError as ex:
        logger.bind(
            path=str(data_dir / DB_DIR / DB_FILENAME),
            error=ex,
            error_type=type(ex).__name__,
        ).warning("Не удалось прочитать настройки профилей")
        return {}, {}, {}
    finally:
        connection.close()
    return stock, photo_stock, refills


__all__ = ["list_profile_views", "list_profiles_response"]
