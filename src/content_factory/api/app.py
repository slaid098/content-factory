"""FastAPI application factory."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from content_factory.api.device_gate import DeviceGateMiddleware
from content_factory.api.device_join import join_router
from content_factory.api.ui_page import UI_DIR, ui_router
from content_factory.api.v1.routes.content import content_router
from content_factory.api.v1.routes.profiles import profiles_router
from content_factory.api.v1.routes.queries import queries_router
from content_factory.api.v1.routes.settings import settings_router
from content_factory.api.v1.routes.stock import stock_router
from content_factory.api.v1.version import version_router
from content_factory.config.settings import Settings
from content_factory.services.delivery import (
    create_cleanup_scheduler,
    run_startup_cleanup,
)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Run one archive cleanup and keep the hourly scheduler running."""
    settings = Settings()
    await asyncio.to_thread(
        run_startup_cleanup,
        settings.data_dir,
        settings.delivery_retention_hours,
    )
    scheduler = create_cleanup_scheduler(
        settings.data_dir,
        settings.delivery_retention_hours,
    )
    scheduler.start()
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    app = FastAPI(title="content-factory", version="0.1.0", lifespan=_lifespan)
    app.add_middleware(DeviceGateMiddleware)
    app.include_router(version_router)
    app.include_router(stock_router, prefix="/api/v1", tags=["stock"])
    app.include_router(content_router, prefix="/api/v1", tags=["content"])
    app.include_router(profiles_router, prefix="/api/v1", tags=["content"])
    app.include_router(queries_router, prefix="/api/v1", tags=["content"])
    app.include_router(settings_router, prefix="/api/v1", tags=["content"])
    app.include_router(join_router)
    app.include_router(ui_router)
    app.mount("/ui", StaticFiles(directory=UI_DIR, check_dir=False), name="ui")
    return app
