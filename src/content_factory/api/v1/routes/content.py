"""Content delivery endpoint."""

from __future__ import annotations

import hmac
from typing import Annotated
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import FileResponse, RedirectResponse
from loguru import logger

from content_factory.config.runtime import load_runtime_config
from content_factory.config.settings import Settings, get_settings
from content_factory.constants import CONFIG_DIR, CONFIG_FILENAME, LANGUAGE_CODE_RE
from content_factory.exceptions.errors import DeliveryBusyError, DeliveryError
from content_factory.exceptions.generator import ProfileError
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
from content_factory.services.profiles import get_profile, is_safe_profile_id

content_router = APIRouter()


def require_delivery_token(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> Settings:
    """Accept a trusted-device cookie or the Bearer token of this request.

    The partner browser proves itself with the device cookie checked by
    :class:`~content_factory.api.device_gate.DeviceGateMiddleware`; the
    YouTube Kit worker has no cookie and sends its own Bearer token.

    Args:
        request: Incoming request; the gate marks it ``device_trusted`` after a
            successful cookie check.
        authorization: Raw ``Authorization`` header of a service client.

    Returns:
        Settings of this request.

    Raises:
        HTTPException: 503 when delivery is not configured, 401 otherwise.
    """
    settings = get_settings()
    if settings.delivery_token is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="delivery is disabled",
        )
    if getattr(request.state, "device_trusted", False):
        return settings
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


def require_safe_profile_id(profile_id: str) -> None:
    """Reject a profile id that could escape the registry directory."""
    if not is_safe_profile_id(profile_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="unsafe profile id",
        )


def profile_error(ex: ProfileError, profile_id: str | None = None) -> HTTPException:
    """Map a missing or malformed profile to 404 without exposing details.

    The exception may carry absolute paths or YAML fragments, so it is only
    written to the server log. The client always gets a fixed message.
    """
    logger.bind(
        profile_id=profile_id,
        error=ex,
        error_type=type(ex).__name__,
    ).warning("Профиль недоступен")
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="profile not found",
    )


def _check_delivery_profile(settings: Settings, request: DeliveryRequest) -> str | None:
    """Return the registry language of the request profile.

    The profile language resolves the address, so a profile request may omit
    ``language``. When the client sends a language, it must match the profile:
    a ``dating/en`` profile cannot be delivered through a ``ru`` address. The
    client gets a fixed message without paths or registry details.

    Args:
        settings: Settings of this request.
        request: Validated delivery request.

    Returns:
        Language of the profile, or ``None`` for a General request.

    Raises:
        HTTPException: The profile is unsafe or missing, or its language does
            not match the sent language.
    """
    if request.profile is None:
        return None
    require_safe_profile_id(request.profile)
    try:
        profile = get_profile(settings.data_dir, request.profile)
    except ProfileError as ex:
        raise profile_error(ex, request.profile) from ex
    if request.language is not None and profile.language != request.language:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="profile language does not match language",
        )
    return profile.language


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
            "description": "Zip archive with NNN.<media> and NNN.json entries",
            "content": {"application/zip": {}},
        },
        status.HTTP_204_NO_CONTENT: {"description": "The address has no deliverable stock"},
        status.HTTP_401_UNAUTHORIZED: {"description": "Missing or invalid token"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Delivery is disabled"},
    },
)
def create_delivery(
    request: DeliveryRequest,
    settings: Annotated[Settings, Depends(require_delivery_token)],
) -> Response:
    """Return a zip with up to ``count`` videos or photos of one address."""
    _check_delivery_limit(request, settings)
    resolved = request.resolve(_check_delivery_profile(settings, request))
    try:
        archive = deliver(
            settings.data_dir,
            resolved.language,
            resolved.count,
            resolved.request_id,
            query=resolved.query,
            profile=resolved.profile,
            content_type=resolved.type,
            retention_hours=settings.delivery_retention_hours,
        )
    except ProfileError as ex:
        raise profile_error(ex, resolved.profile) from ex
    except DeliveryError as ex:
        logger.bind(
            request_id=str(resolved.request_id),
            error=ex,
            error_type=type(ex).__name__,
        ).error("Не удалось собрать выдачу {}", resolved.request_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="delivery failed",
        ) from ex
    if archive is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return FileResponse(
        archive,
        media_type="application/zip",
        filename=f"delivery-{resolved.request_id}.zip",
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
    """Start a background build and Filebin upload for a manual delivery."""
    _check_delivery_limit(request, settings)
    resolved = request.resolve(_check_delivery_profile(settings, request))
    try:
        return start_delivery_job(
            settings.data_dir,
            resolved,
            retention_hours=settings.delivery_retention_hours,
        )
    except ProfileError as ex:
        raise profile_error(ex, resolved.profile) from ex
    except DeliveryBusyError as ex:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="another delivery is already building",
        ) from ex
    except DeliveryError as ex:
        logger.bind(
            request_id=str(resolved.request_id),
            error=ex,
            error_type=type(ex).__name__,
        ).error("Не удалось запустить выдачу {}", resolved.request_id)
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
    language: Annotated[str, Path(pattern=LANGUAGE_CODE_RE.pattern)],
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
    language: Annotated[str, Query(pattern=LANGUAGE_CODE_RE.pattern)],
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
        status.HTTP_302_FOUND: {"description": "Redirect to a Filebin archive"},
        status.HTTP_401_UNAUTHORIZED: {"description": "Missing or invalid token"},
        status.HTTP_404_NOT_FOUND: {"description": "Archive is missing or expired"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Delivery is disabled"},
    },
)
def download_archive(
    language: Annotated[str, Path(pattern=LANGUAGE_CODE_RE.pattern)],
    request_id: Annotated[UUID, Path()],
    settings: Annotated[Settings, Depends(require_delivery_token)],
) -> Response:
    """Return a stored archive, redirecting to Filebin for remote archives."""
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
