"""Generator profile endpoints."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
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
from content_factory.constants import (
    CONFIG_DIR,
    CONFIG_FILENAME,
    LANGUAGE_CODE_RE,
    NICHE_CODE_RE,
)
from content_factory.exceptions.errors import (
    ConfigError,
    ProfilePreviewError,
    ProfileStorageError,
    ProfileValidationError,
)
from content_factory.exceptions.generator import ProfileError
from content_factory.schemas.config.runtime import GeneratorConfig
from content_factory.schemas.profiles.api import (
    ProfileDetailResponse,
    ProfilePrompts,
    ProfileResponse,
    ProfilesResponse,
    ProfileUpdateRequest,
    PromptCardResponse,
    PromptFile,
    PromptTextRequest,
)
from content_factory.services.profiles import (
    list_profiles_response,
    preview_for_profile,
    update_profile_settings,
)
from content_factory.services.profiles.service import (
    get_profile_detail,
    read_profile_prompts,
    write_card_prompt,
    write_prompt_template,
    write_text_prompt,
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
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Invalid language or niche format"},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Profiles cannot be read"},
    },
)
def list_profiles(
    settings: Annotated[Settings, Depends(require_delivery_token)],
    language: Annotated[str | None, Query(pattern=LANGUAGE_CODE_RE.pattern)] = None,
    niche: Annotated[str | None, Query(pattern=NICHE_CODE_RE.pattern)] = None,
) -> ProfilesResponse:
    """Return the filtered profiles with their stock, refill and filter catalog."""
    output_dir, global_config = _context(settings)
    return list_profiles_response(
        settings.data_dir,
        output_dir=output_dir,
        global_config=global_config,
        language=language,
        niche=niche,
    )


@profiles_router.get(
    "/content/profiles/{category}/{name}",
    response_model=ProfileDetailResponse,
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
) -> ProfileDetailResponse:
    """Return one profile with its recipe and safe settings."""
    profile_id = _profile_id(category, name)
    output_dir, global_config = _context(settings)
    try:
        return get_profile_detail(
            settings.data_dir,
            profile_id,
            output_dir=output_dir,
            global_config=global_config,
        )
    except ProfileError as ex:
        raise profile_error(ex, profile_id) from ex


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
        raise profile_error(ex, profile_id) from ex
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
    "/content/profiles/{category}/{name}/prompts",
    response_model=ProfilePrompts,
    responses={
        **_AUTH_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "Profile is missing or broken"},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Unsafe profile id"},
    },
)
def get_profile_prompts(
    category: Annotated[str, PathParam(min_length=1, max_length=100)],
    name: Annotated[str, PathParam(min_length=1, max_length=100)],
    settings: Annotated[Settings, Depends(require_delivery_token)],
) -> ProfilePrompts:
    """Return the template, cards and photo prompt of one profile."""
    profile_id = _profile_id(category, name)
    try:
        return read_profile_prompts(settings.data_dir, profile_id)
    except ProfileError as ex:
        raise profile_error(ex, profile_id) from ex


@profiles_router.put(
    "/content/profiles/{category}/{name}/prompts/template",
    response_model=PromptFile,
    responses={
        **_AUTH_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "Profile is missing or broken"},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Invalid prompt text"},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Prompt cannot be written"},
    },
)
def put_prompt_template(
    category: Annotated[str, PathParam(min_length=1, max_length=100)],
    name: Annotated[str, PathParam(min_length=1, max_length=100)],
    payload: PromptTextRequest,
    settings: Annotated[Settings, Depends(require_delivery_token)],
) -> PromptFile:
    """Replace the scene template used by one profile."""
    profile_id = _profile_id(category, name)
    return _write_prompt(
        profile_id,
        lambda: write_prompt_template(settings.data_dir, profile_id, payload.text),
    )


@profiles_router.put(
    "/content/profiles/{category}/{name}/prompts/cards/{look_id}",
    response_model=PromptCardResponse,
    responses={
        **_AUTH_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "Profile or card is missing"},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Invalid prompt text"},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Prompt cannot be written"},
    },
)
def put_prompt_card(
    category: Annotated[str, PathParam(min_length=1, max_length=100)],
    name: Annotated[str, PathParam(min_length=1, max_length=100)],
    look_id: Annotated[str, PathParam(min_length=1, max_length=100)],
    payload: PromptTextRequest,
    settings: Annotated[Settings, Depends(require_delivery_token)],
) -> PromptCardResponse:
    """Store or clear one appearance card prompt override."""
    profile_id = _profile_id(category, name)
    return _write_prompt(
        profile_id,
        lambda: write_card_prompt(settings.data_dir, profile_id, look_id, payload.text),
    )


@profiles_router.put(
    "/content/profiles/{category}/{name}/prompts/text",
    response_model=PromptFile,
    responses={
        **_AUTH_RESPONSES,
        status.HTTP_404_NOT_FOUND: {"description": "Profile has no photo layer"},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Invalid prompt text"},
        status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Prompt cannot be written"},
    },
)
def put_prompt_text(
    category: Annotated[str, PathParam(min_length=1, max_length=100)],
    name: Annotated[str, PathParam(min_length=1, max_length=100)],
    payload: PromptTextRequest,
    settings: Annotated[Settings, Depends(require_delivery_token)],
) -> PromptFile:
    """Replace the photo text prompt of one profile."""
    profile_id = _profile_id(category, name)
    return _write_prompt(
        profile_id,
        lambda: write_text_prompt(settings.data_dir, profile_id, payload.text),
    )


@profiles_router.get(
    "/content/profiles/{category}/{name}/preview",
    response_class=Response,
    responses={
        **_AUTH_RESPONSES,
        status.HTTP_200_OK: {
            "description": "JPEG frame of the newest video or the newest photo post",
            "content": {
                "image/jpeg": {},
                "image/png": {},
                "image/webp": {},
            },
        },
        status.HTTP_404_NOT_FOUND: {"description": "Profile is missing or has no media yet"},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Unsafe profile id"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Preview cannot be built"},
    },
)
def get_profile_preview(
    category: Annotated[str, PathParam(min_length=1, max_length=100)],
    name: Annotated[str, PathParam(min_length=1, max_length=100)],
    settings: Annotated[Settings, Depends(require_delivery_token)],
) -> Response:
    """Return a preview of the newest profile video or its newest photo post."""
    profile_id = _profile_id(category, name)
    try:
        preview = preview_for_profile(settings.data_dir, profile_id)
    except ProfileError as ex:
        raise profile_error(ex, profile_id) from ex
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
        preview.path,
        media_type=preview.media_type,
        headers={
            "Cache-Control": "max-age=60",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _write_prompt[T](profile_id: str, action: Callable[[], T]) -> T:
    """Map prompt validation, missing profile and write failures to HTTP errors."""
    try:
        return action()
    except ProfileError as ex:
        raise profile_error(ex, profile_id) from ex
    except ProfileValidationError as ex:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(ex),
        ) from ex
    except ProfileStorageError as ex:
        logger.bind(
            profile_id=profile_id,
            error=ex,
            error_type=type(ex).__name__,
        ).error("Не удалось записать промпт профиля {}", profile_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="prompt write failed",
        ) from ex


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
