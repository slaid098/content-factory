"""Runtime settings endpoints."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger

from content_factory.api.v1.routes.content import require_delivery_token
from content_factory.config.settings import Settings, load_generator_settings
from content_factory.exceptions.errors import (
    ConfigError,
    SettingsValidationError,
    SettingsWriteError,
)
from content_factory.schemas.settings.api import (
    GeneratorSettingsResponse,
    RuntimeSettings,
    SettingsApplyResponse,
    SettingsPreviewResponse,
)
from content_factory.services.settings import apply_settings, preview_settings, read_settings

settings_router = APIRouter()

_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_401_UNAUTHORIZED: {"description": "Missing or invalid token"},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Delivery is disabled"},
}
_READ_RESPONSES: dict[int | str, dict[str, Any]] = {
    **_RESPONSES,
    status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Settings cannot be read"},
}
_EDIT_RESPONSES: dict[int | str, dict[str, Any]] = {
    **_RESPONSES,
    status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Invalid settings"},
    status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Settings cannot be written"},
}


@settings_router.get(
    "/content/settings",
    response_model=RuntimeSettings,
    responses=_READ_RESPONSES,
)
def get_settings(
    settings: Annotated[Settings, Depends(require_delivery_token)],
) -> RuntimeSettings:
    """Return the safe editable slice of the runtime configuration."""
    try:
        return read_settings(settings.data_dir)
    except ConfigError as ex:
        logger.bind(error=ex, error_type=type(ex).__name__).error("Не удалось прочитать настройки")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(ex),
        ) from ex


@settings_router.get(
    "/content/settings/generator",
    response_model=GeneratorSettingsResponse,
    responses=_READ_RESPONSES,
)
def get_generator_settings(
    settings: Annotated[Settings, Depends(require_delivery_token)],
) -> GeneratorSettingsResponse:
    """Return the safe read-only slice of the generator settings."""
    try:
        generator = load_generator_settings()
    except ConfigError as ex:
        logger.bind(error=ex, error_type=type(ex).__name__).error(
            "Не удалось прочитать настройки генератора"
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="generator settings unavailable",
        ) from ex
    return GeneratorSettingsResponse(
        rent_pool_size=generator.rent_pool_size,
        videos_per_server=generator.videos_per_server,
        max_servers=generator.max_servers,
        offer_refresh_seconds=generator.offer_refresh_seconds,
        server_ready_timeout=generator.server_ready_timeout,
        comfy_health_timeout=generator.comfy_health_timeout,
        comfy_job_timeout=generator.comfy_job_timeout,
        comfy_health_interval=generator.comfy_health_interval,
        max_cost_per_hour=generator.max_cost_per_hour,
        stale_instance_hours=generator.stale_instance_hours,
        safety_enabled=generator.safety_enabled,
        safety_frames=generator.safety_frames,
        safety_threshold=generator.safety_threshold,
        safety_nude_threshold=generator.safety_nude_threshold,
        safety_frame_attempts=generator.safety_frame_attempts,
        model=generator.model,
    )


@settings_router.post(
    "/content/settings/preview",
    response_model=SettingsPreviewResponse,
    responses=_EDIT_RESPONSES,
)
def preview(
    payload: RuntimeSettings,
    settings: Annotated[Settings, Depends(require_delivery_token)],
) -> SettingsPreviewResponse:
    """Validate the settings and return a unified diff without writing."""
    return _run_edit(lambda: preview_settings(settings.data_dir, payload))


@settings_router.put(
    "/content/settings",
    response_model=SettingsApplyResponse,
    responses=_EDIT_RESPONSES,
)
def apply(
    payload: RuntimeSettings,
    settings: Annotated[Settings, Depends(require_delivery_token)],
) -> SettingsApplyResponse:
    """Validate the settings, back up the file and replace it atomically."""
    return _run_edit(lambda: apply_settings(settings.data_dir, payload))


def _run_edit[T](action: Callable[[], T]) -> T:
    """Map settings validation and write failures to HTTP errors."""
    try:
        return action()
    except (SettingsValidationError, ConfigError) as ex:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(ex),
        ) from ex
    except SettingsWriteError as ex:
        logger.bind(error=ex, error_type=type(ex).__name__).error("Не удалось записать настройки")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="settings write failed",
        ) from ex
