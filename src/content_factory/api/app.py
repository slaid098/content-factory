"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from content_factory.api.v1.routes.stock import stock_router


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    app = FastAPI(title="content-factory", version="0.1.0")
    app.include_router(stock_router, prefix="/api/v1", tags=["stock"])
    return app
