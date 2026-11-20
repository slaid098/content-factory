"""Content delivery endpoint."""

from __future__ import annotations

import hmac
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Response, status
from fastapi.responses import FileResponse, RedirectResponse
from loguru import logger

from content_factory.config.runtime import load_runtime_config
from content_factory.config.settings import Settings
from content_factory.constants import CONFIG_DIR, CONFIG_FILENAME
from content_factory.exceptions.errors import DeliveryBusyError, DeliveryError
from content_factory.schemas.content.archives import ArchiveInfo
from content_factory.schemas.content.delivery import DeliveryJobInfo, DeliveryRequest
from content_factory.schemas.content.topics import TopicsResponse
from content_factory.services.delivery import (
    collect_topics,
    deliver,
    get_delivery_job,
    list_archives,
    resolve_archive,
    start_delivery_job,
)

content_router = APIRouter()

_LANGUAGE_PATTERN = r"^[a-z]{2}$"


def require_delivery_token(
    authorization: Annotated[str | None, Header()] = None,
) -> Settings:
    """Check the Bearer token and return the settings of this request.

    Raises:
        HTTPException: 503 when delivery is not configured, 401 otherwise.
    """
    settings = Settings()
    if settings.delivery_token is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="delivery is disabled",
        )
    token = _bearer_token(authorization)
    if token is None or not hmac.compare_digest(
        token.encode(),
        settings.delivery_token.encode(),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid delivery token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return settings


def _bearer_token(header: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer`` header."""
    if header is None:
        return None
    scheme, separator, token = header.partition(" ")
    token = token.strip()
    if not separator or scheme.lower() != "bearer" or not token:
        return None
    return token


def _check_delivery_limit(request: DeliveryRequest, settings: Settings) -> None:
    """Reject a count above the runtime ``delivery.max_count`` limit."""
    runtime = load_runtime_config(
        settings.data_dir / CONFIG_DIR / CONFIG_FILENAME,
        seed=False,
    )
    limit = runtime.delivery.max_count
    if request.count > limit:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"count exceeds delivery.max_count ({limit})",
        )


@content_router.post(
    "/content/deliveries",
    response_class=Response,
    responses={
        status.HTTP_200_OK: {
            "description": "Zip archive with NNN.mp4 and NNN.json entries",
            "content": {"application/zip": {}},
        },
        status.HTTP_204_NO_CONTENT: {"description": "The language has no mp4 files"},
        status.HTTP_401_UNAUTHORIZED: {"description": "Missing or invalid token"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Delivery is disabled"},
    },
)
def create_delivery(
    request: DeliveryRequest,
    settings: Annotated[Settings, Depends(require_delivery_token)],
) -> Response:
    """Return a zip with up to ``count`` videos of one language."""
    _check_delivery_limit(request, settings)
    try:
        archive = deliver(
            settings.data_dir,
            request.language,
            request.count,
            request.request_id,
            query=request.query,
            retention_hours=settings.delivery_retention_hours,
        )
    except DeliveryError as ex:
        logger.bind(
            request_id=str(request.request_id),
            error=ex,
            error_type=type(ex).__name__,
        ).error("Не удалось собрать выдачу {}", request.request_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="delivery failed",
        ) from ex
    if archive is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return FileResponse(
        archive,
        media_type="application/zip",
        filename=f"delivery-{request.request_id}.zip",
    )


@content_router.post(
    "/content/deliveries/link",
    response_model=DeliveryJobInfo,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_202_ACCEPTED: {"description": "Build and upload started"},
        status.HTTP_401_UNAUTHORIZED: {"description": "Missing or invalid token"},
        status.HTTP_409_CONFLICT: {"description": "Another build is already running"},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Invalid body or count"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Delivery is disabled"},
    },
)
def create_delivery_link(
    request: DeliveryRequest,
    settings: Annotated[Settings, Depends(require_delivery_token)],
) -> DeliveryJobInfo:
    """Start a background build and Litterbox upload for a manual delivery."""
    _check_delivery_limit(request, settings)
    try:
        return start_delivery_job(
            settings.data_dir,
            request,
            retention_hours=settings.delivery_retention_hours,
        )
    except DeliveryBusyError as ex:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="another delivery is already building",
        ) from ex
    except DeliveryError as ex:
        logger.bind(
            request_id=str(request.request_id),
            error=ex,
            error_type=type(ex).__name__,
        ).error("Не удалось запустить выдачу {}", request.request_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="delivery failed",
        ) from ex


@content_router.get(
    "/content/deliveries/{language}/{request_id}",
    response_model=DeliveryJobInfo,
    responses={
        status.HTTP_200_OK: {"description": "Current job state"},
        status.HTTP_401_UNAUTHORIZED: {"description": "Missing or invalid token"},
        status.HTTP_404_NOT_FOUND: {"description": "Job is missing or expired"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Delivery is disabled"},
    },
)
def get_delivery(
    language: Annotated[str, Path(pattern=_LANGUAGE_PATTERN)],
    request_id: Annotated[UUID, Path()],
    settings: Annotated[Settings, Depends(require_delivery_token)],
) -> DeliveryJobInfo:
    """Return the state of one temporary-link delivery job."""
    job = get_delivery_job(
        settings.data_dir,
        language,
        request_id,
        retention_hours=settings.delivery_retention_hours,
    )
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="delivery not found",
        )
    return job


@content_router.get(
    "/content/topics",
    response_model=TopicsResponse,
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Missing or invalid token"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Delivery is disabled"},
    },
)
def get_topics(
    language: Annotated[str, Query(pattern=_LANGUAGE_PATTERN)],
    settings: Annotated[Settings, Depends(require_delivery_token)],
) -> TopicsResponse:
    """Return the stock breakdown by search topic for one language."""
    return collect_topics(settings.data_dir, language)


@content_router.get(
    "/content/archives",
    response_model=tuple[ArchiveInfo, ...],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Missing or invalid token"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Delivery is disabled"},
    },
)
def get_archives(
    settings: Annotated[Settings, Depends(require_delivery_token)],
) -> tuple[ArchiveInfo, ...]:
    """Return live delivery archives, newest first."""
    return list_archives(settings.data_dir, settings.delivery_retention_hours)


@content_router.get(
    "/content/archives/{language}/{request_id}.zip",
    response_class=Response,
    responses={
        status.HTTP_200_OK: {
            "description": "Stored zip archive",
            "content": {"application/zip": {}},
        },
        status.HTTP_302_FOUND: {"description": "Redirect to a Litterbox archive"},
        status.HTTP_401_UNAUTHORIZED: {"description": "Missing or invalid token"},
        status.HTTP_404_NOT_FOUND: {"description": "Archive is missing or expired"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Delivery is disabled"},
    },
)
def download_archive(
    language: Annotated[str, Path(pattern=_LANGUAGE_PATTERN)],
    request_id: Annotated[UUID, Path()],
    settings: Annotated[Settings, Depends(require_delivery_token)],
) -> Response:
    """Return a stored archive, redirecting to Litterbox for remote archives."""
    location = resolve_archive(
        settings.data_dir,
        language,
        request_id,
        settings.delivery_retention_hours,
    )
    if location is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="archive not found",
        )
    if location.storage == "remote":
        if location.url is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="archive not found",
            )
        return RedirectResponse(location.url, status_code=status.HTTP_302_FOUND)
    if location.path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="archive not found",
        )
    return FileResponse(
        location.path,
        media_type="application/zip",
        filename=f"delivery-{request_id}.zip",
    )
