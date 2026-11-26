"""One-time trusted-device join route.

``GET /join/{token}`` consumes the link, sets the permanent ``device_token``
cookie and redirects to ``/`` so the token leaves the address bar. The route is
mounted before the partner page and carries no gate: a new device has no cookie
yet. An unknown, used, expired or revoked link answers the same 410 page.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, status
from fastapi.responses import RedirectResponse, Response
from loguru import logger

from content_factory.api.device_gate import DEVICE_COOKIE_NAME
from content_factory.api.device_pages import invalid_link_page, service_unavailable_page
from content_factory.config.settings import Settings, get_settings
from content_factory.constants import DB_DIR, DB_FILENAME
from content_factory.db.errors import DatabaseError
from content_factory.services.device_trust import redeem_link

join_router = APIRouter(tags=["device-access"], include_in_schema=False)


@join_router.get("/join", status_code=status.HTTP_410_GONE)
def join_without_token() -> Response:
    """A bare ``/join`` hides the cause: answer the same 410 page as a bad token."""
    return invalid_link_page()


@join_router.get("/join/{token}", status_code=status.HTTP_303_SEE_OTHER)
def join_device(token: str) -> Response:
    """Redeem the one-time link and trust this browser."""
    settings = get_settings()
    try:
        redeemed = redeem_link(_db_path(settings), token)
    except (DatabaseError, OSError) as ex:
        logger.bind(error=ex, error_type=type(ex).__name__).error(
            "device join: link redemption failed"
        )
        return service_unavailable_page()
    if redeemed is None:
        return invalid_link_page()
    max_age = max(0, int((redeemed.expires_at - datetime.now(UTC)).total_seconds()))
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key=DEVICE_COOKIE_NAME,
        value=redeemed.token,
        max_age=max_age,
        path="/",
        httponly=True,
        samesite="lax",
        secure=settings.device_cookie_secure,
    )
    return response


def _db_path(settings: Settings) -> Path:
    """Return the operational database path of the service."""
    return settings.data_dir / DB_DIR / DB_FILENAME


__all__ = ["join_router"]
