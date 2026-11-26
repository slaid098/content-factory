"""Trusted-device gate middleware.

Every request except the one-time join route and the deployment probes must
carry an active ``device_token`` cookie. The check asks the device-trust capsule
on each request, so revoking a device denies the very next request.

Two paths stay open. ``/join`` is the only way to earn a cookie. The stock
endpoint of the local deploy healthcheck answers ``{"status": "ok"}`` only for
a loopback ``Host`` without proxy headers and never returns stock data, so
``compose.yaml`` and ``scripts/deploy.sh`` keep working without a cookie.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import Request, status
from fastapi.responses import JSONResponse, Response
from loguru import logger
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from content_factory.api.device_pages import (
    access_denied_page,
    service_unavailable_page,
)
from content_factory.config.settings import Settings, get_settings
from content_factory.constants import DB_DIR, DB_FILENAME
from content_factory.exceptions.errors import DatabaseError
from content_factory.services.device_trust import authenticate, database_ready

DEVICE_COOKIE_NAME = "device_token"

_JOIN_PATH = "/join"
_API_PREFIX = "/api/"
_CONTENT_PREFIX = "/api/v1/content/"
_HEALTH_PATH = "/api/v1/stock"
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})
_PROXY_HEADERS = ("x-forwarded-for", "x-real-ip")


def _is_open(request: Request) -> bool:
    """Return True when the request needs no device cookie."""
    path = request.url.path
    if path.startswith(_CONTENT_PREFIX):
        return True
    return request.method == "GET" and (path == _JOIN_PATH or path.startswith(f"{_JOIN_PATH}/"))


def _is_local_healthcheck(request: Request) -> bool:
    """Return True for the loopback deploy probe without proxy headers."""
    if request.method != "GET" or request.url.path != _HEALTH_PATH:
        return False
    if any(name in request.headers for name in _PROXY_HEADERS):
        return False
    host = request.headers.get("host", "").strip().lower()
    name = host.partition(":")[0]
    return name in _LOCAL_HOSTS


def _deny(request: Request) -> Response:
    """Answer 403: JSON for API clients, a self-contained page otherwise."""
    if request.url.path.startswith(_API_PREFIX):
        return JSONResponse(
            {"detail": "device not trusted"},
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return access_denied_page()


def _unavailable(request: Request) -> Response:
    """Answer 503 when the device lookup itself failed (DB outage)."""
    if request.url.path.startswith(_API_PREFIX):
        return JSONResponse(
            {"detail": "device check unavailable"},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return service_unavailable_page()


def _db_path(settings: Settings) -> Path:
    """Return the operational database path of the service."""
    return settings.data_dir / DB_DIR / DB_FILENAME


class DeviceGateMiddleware(BaseHTTPMiddleware):
    """Require an active trusted-device cookie before reaching the app."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        """Pass open paths and trusted devices; deny everybody else."""
        settings = get_settings()
        if not settings.device_gate_enabled:
            return await call_next(request)
        if _is_open(request):
            return await call_next(request)
        if _is_local_healthcheck(request):
            return await self._healthcheck(settings)
        token = request.cookies.get(DEVICE_COOKIE_NAME, "")
        if not token:
            return _deny(request)
        try:
            device = await asyncio.to_thread(authenticate, _db_path(settings), token)
        except (DatabaseError, OSError) as ex:
            logger.bind(error=ex, error_type=type(ex).__name__).error(
                "device gate: trusted-device check failed"
            )
            return _unavailable(request)
        if device is None:
            return _deny(request)
        return await call_next(request)

    async def _healthcheck(self, settings: Settings) -> Response:
        """Probe the database and answer ``{"status": "ok"}`` without stock."""
        try:
            await asyncio.to_thread(database_ready, _db_path(settings))
        except (DatabaseError, OSError) as ex:
            logger.bind(error=ex, error_type=type(ex).__name__).error(
                "device gate: local healthcheck failed"
            )
            return JSONResponse(
                {"detail": "device check unavailable"},
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return JSONResponse({"status": "ok"})


__all__ = ["DEVICE_COOKIE_NAME", "DeviceGateMiddleware"]
