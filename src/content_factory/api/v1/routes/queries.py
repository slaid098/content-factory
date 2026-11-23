"""Language search queries endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger

from content_factory.api.v1.routes.content import require_delivery_token
from content_factory.config.runtime import load_runtime_config
from content_factory.config.settings import Settings
from content_factory.constants import CONFIG_DIR, CONFIG_FILENAME
from content_factory.exceptions.errors import DeliveryError
from content_factory.schemas.content.queries import (
    QueriesResponse,
    QueryCreateRequest,
    QueryCreateResponse,
)
from content_factory.services.delivery import add_language_query, list_language_queries

queries_router = APIRouter()

_LANGUAGE_PATTERN = r"^[a-z]{2}$"
_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_401_UNAUTHORIZED: {"description": "Missing or invalid token"},
    status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Invalid language or query"},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Delivery is disabled"},
}


def _require_configured_language(settings: Settings, language: str) -> None:
    """Reject a language that is not present in the runtime config."""
    runtime = load_runtime_config(
        settings.data_dir / CONFIG_DIR / CONFIG_FILENAME,
        seed=False,
    )
    if language not in runtime.languages:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="unknown language",
        )


@queries_router.get(
    "/content/queries",
    response_model=QueriesResponse,
    responses=_RESPONSES,
)
def get_queries(
    language: Annotated[str, Query(pattern=_LANGUAGE_PATTERN)],
    settings: Annotated[Settings, Depends(require_delivery_token)],
) -> QueriesResponse:
    """Return every search query of one language with its stock counts."""
    _require_configured_language(settings, language)
    try:
        return list_language_queries(settings.data_dir, language)
    except DeliveryError as ex:
        logger.bind(
            language=language,
            error=ex,
            error_type=type(ex).__name__,
        ).error("Не удалось прочитать список запросов")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="queries unavailable",
        ) from ex


@queries_router.post(
    "/content/queries",
    response_model=QueryCreateResponse,
    responses=_RESPONSES,
)
def create_query(
    request: QueryCreateRequest,
    settings: Annotated[Settings, Depends(require_delivery_token)],
) -> QueryCreateResponse:
    """Append one search query to the language file."""
    _require_configured_language(settings, request.language)
    try:
        query, created = add_language_query(
            settings.data_dir,
            request.language,
            request.query,
        )
    except ValueError as ex:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(ex),
        ) from ex
    except DeliveryError as ex:
        logger.bind(
            language=request.language,
            error=ex,
            error_type=type(ex).__name__,
        ).error("Не удалось добавить запрос")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="queries unavailable",
        ) from ex
    return QueryCreateResponse(
        language=request.language,
        query=query,
        created=created,
    )
