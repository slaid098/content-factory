"""Partner delivery page served by the API."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

UI_DIR = Path(__file__).with_name("ui")

ui_router = APIRouter()


@ui_router.get("/", response_class=FileResponse, include_in_schema=False)
def get_ui_page() -> FileResponse:
    """Return the partner delivery page.

    The page itself is public: the reverse proxy limits access by IP and
    injects the delivery token for ``/api/v1/content/`` requests.
    """
    return FileResponse(
        UI_DIR / "index.html",
        media_type="text/html",
        headers={"Cache-Control": "no-cache"},
    )
