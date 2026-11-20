"""Content delivery endpoint."""

from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from fastapi.responses import FileResponse
from loguru import logger

from content_factory.config.settings import Settings
from content_factory.exceptions.errors import DeliveryError
from content_factory.schemas.content.delivery import DeliveryRequest
from content_factory.services.delivery import deliver

content_router = APIRouter()


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
    try:
        archive = deliver(
            settings.data_dir,
            request.language,
            request.count,
            request.request_id,
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
