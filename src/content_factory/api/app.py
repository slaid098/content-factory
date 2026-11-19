"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    return FastAPI(title="content-factory", version="0.1.0")
