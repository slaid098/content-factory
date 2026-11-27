"""Release identity endpoint.

The endpoint is open and never touches the database: the deploy and release
jobs read it over loopback to agree on the SHA that is actually live.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter

from content_factory.config.settings import get_settings
from content_factory.schemas.version import VersionResponse

version_router = APIRouter(tags=["version"])


@version_router.get("/api/version", response_model=VersionResponse)
def get_version() -> VersionResponse:
    """Return the SHA and build time baked into the image."""
    settings = get_settings()
    return VersionResponse(
        version=settings.git_sha,
        built_at=_parse_built_at(settings.built_at),
    )


def _parse_built_at(value: str | None) -> datetime | None:
    """Parse ``BUILT_AT``; a missing, empty or unknown value is null."""
    if value is None or value == "unknown":
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment
