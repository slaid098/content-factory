"""Generator profile endpoints."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from loguru import logger

from content_factory.api.v1.routes.content import (
    profile_error,
    require_delivery_token,
    require_safe_profile_id,
)
from content_factory.config.runtime import load_runtime_config
from content_factory.config.settings import Settings, load_generator_settings
from content_factory.constants import CONFIG_DIR, CONFIG_FILENAME
from content_factory.exceptions.errors import (
    ConfigError,
    ProfilePreviewError,
    ProfileStorageError,
    ProfileValidationError,
)
from content_factory.exceptions.generator import ProfileError
from content_factory.schemas.config.runtime import GeneratorConfig
from content_factory.schemas.profiles.api import (
    ProfileResponse,
    ProfilesResponse,
    ProfileUpdateRequest,
)
from content_factory.services.profiles import (
    get_profile_view,
    list_profile_views,
    preview_for_profile,
    update_profile_settings,
)

profiles_router = APIRouter()

_AUTH_RESPONSES: dict[int | str, dict[str, str]] = {
    status.HTTP_401_UNAUTHORIZED: {"description": "Missing or invalid token"},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Delivery is disabled"},
}


@profiles_router.get(
    "/content/profiles",
    response_model=ProfilesResponse,
    responses={
        **_AUTH_RESPONSES,
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Profiles cannot be read"},
    },
)
def list_profiles(
    settings: Annotated[Settings, Depends(require_delivery_token)],
) -> ProfilesResponse:
    """Return every readable profile with its stock and refill settings."""
    output_dir, global_config = _context(settings)
    return ProfilesResponse(
        profiles=list_profile_views(
            settings.data_dir,
            output_dir=output_dir,
            global_config=global_config,
        )
    )


@profiles_router.get(
    "/content/profiles/{category}/{name}",
    response_model=ProfileResponse,
    responses={
        **_AUTH_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "Profile is missing"},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Unsafe profile id"},
    },
)
def get_profile(
    category: Annotated[str, PathParam(min_length=1, max_length=100)],
    name: Annotated[str, PathParam(min_length=1, max_length=100)],
    settings: Annotated[Settings, Depends(require_delivery_token)],
) -> ProfileResponse:
    """Return one profile."""
    profile_id = _profile_id(category, name)
    output_dir, global_config = _context(settings)
    try:
        return get_profile_view(
            settings.data_dir,
            profile_id,
            output_dir=output_dir,
            global_config=global_config,
        )
    except ProfileError as ex:
        raise profile_error(ex) from ex


@profiles_router.patch(
    "/content/profiles/{category}/{name}",
    response_model=ProfileResponse,
    responses={
        **_AUTH_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "Profile is missing"},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {
            "description": "Unsafe id or invalid refill settings"
        },
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Settings cannot be written"},
    },
)
def update_profile(
    category: Annotated[str, PathParam(min_length=1, max_length=100)],
    name: Annotated[str, PathParam(min_length=1, max_length=100)],
    payload: ProfileUpdateRequest,
    settings: Annotated[Settings, Depends(require_delivery_token)],
) -> ProfileResponse:
    """Apply a partial update of the per-profile refill settings."""
    profile_id = _profile_id(category, name)
    output_dir, global_config = _context(settings)
    try:
        return update_profile_settings(
            settings.data_dir,
            profile_id,
            payload,
            output_dir=output_dir,
            global_config=global_config,
        )
    except ProfileError as ex:
        raise profile_error(ex) from ex
    except ProfileValidationError as ex:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(ex),
        ) from ex
    except ProfileStorageError as ex:
        logger.bind(error=ex, error_type=type(ex).__name__).error(
            "Не удалось сохранить настройки профиля"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="profile settings write failed",
        ) from ex


@profiles_router.get(
    "/content/profiles/{category}/{name}/preview",
    response_class=Response,
    responses={
        **_AUTH_RESPONSES,
        status.HTTP_200_OK: {
            "description": "JPEG frame from the newest profile video",
            "content": {"image/jpeg": {}},
        },
        status.HTTP_404_NOT_FOUND: {"description": "Profile is missing or has no video yet"},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Unsafe profile id"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Preview cannot be built"},
    },
)
def get_profile_preview(
    category: Annotated[str, PathParam(min_length=1, max_length=100)],
    name: Annotated[str, PathParam(min_length=1, max_length=100)],
    settings: Annotated[Settings, Depends(require_delivery_token)],
) -> Response:
    """Return a cached JPEG preview of the newest profile video."""
    profile_id = _profile_id(category, name)
    try:
        preview = preview_for_profile(settings.data_dir, profile_id)
    except ProfileError as ex:
        raise profile_error(ex) from ex
    except ProfilePreviewError as ex:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="preview unavailable",
        ) from ex
    if preview is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="preview not found",
        )
    return FileResponse(
        preview,
        media_type="image/jpeg",
        headers={"Cache-Control": "max-age=60"},
    )


def _profile_id(category: str, name: str) -> str:
    """Build a safe profile id from path segments."""
    profile_id = f"{category}/{name}"
    require_safe_profile_id(profile_id)
    return profile_id


def _context(settings: Settings) -> tuple[Path, GeneratorConfig]:
    """Read the generator output directory and global refill settings."""
    try:
        generator = load_generator_settings()
        runtime = load_runtime_config(
            settings.data_dir / CONFIG_DIR / CONFIG_FILENAME,
            seed=False,
        )
    except ConfigError as ex:
        logger.bind(error=ex, error_type=type(ex).__name__).error(
            "Не удалось прочитать настройки профилей"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="profiles unavailable",
        ) from ex
    return generator.output_dir, runtime.generator
