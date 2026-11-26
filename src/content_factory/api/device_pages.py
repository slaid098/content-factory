"""Self-contained HTML pages for trusted-device access.

The join failure (410) and the gate denial (403/503) are served before the
partner page; they load no external asset, send no referrer and never
interpolate request data, so the one-time token cannot leak through the address
bar into a third-party resource.
"""

from __future__ import annotations

from fastapi import status
from fastapi.responses import HTMLResponse

_PAGE = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="referrer" content="no-referrer">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s</title>
<style>
  body { margin: 0; min-height: 100vh; display: grid; place-items: center;
         background: #0f1115; color: #e6e8ee;
         font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
  main { max-width: 30rem; padding: 2rem; text-align: center; }
  h1 { font-size: 1.35rem; margin: 0 0 0.75rem; }
  p { margin: 0; color: #a7adbb; line-height: 1.5; }
</style>
</head>
<body><main><h1>%(title)s</h1><p>%(message)s</p></main></body>
</html>"""

_HEADERS = {"Cache-Control": "no-store"}


def _page(title: str, message: str, status_code: int) -> HTMLResponse:
    """Render one self-contained page with constant, non-interpolated text."""
    return HTMLResponse(
        _PAGE % {"title": title, "message": message},
        status_code=status_code,
        headers=_HEADERS,
    )


def invalid_link_page() -> HTMLResponse:
    """410 page: the link is unknown, used, expired or revoked."""
    return _page(
        "Ссылка недействительна",
        "Одноразовая ссылка уже использована или устарела. Попросите выдать новую.",
        status.HTTP_410_GONE,
    )


def access_denied_page() -> HTMLResponse:
    """403 page: the browser carries no active trusted-device cookie."""
    return _page(
        "Устройство не доверено",
        "Откройте на этом устройстве одноразовую ссылку /join/…, которую выдал администратор.",
        status.HTTP_403_FORBIDDEN,
    )


def service_unavailable_page() -> HTMLResponse:
    """503 page: the device check itself could not reach the database."""
    return _page(
        "Сервис временно недоступен",
        "Проверка устройства не удалась. Повторите попытку через минуту.",
        status.HTTP_503_SERVICE_UNAVAILABLE,
    )


__all__ = [
    "access_denied_page",
    "invalid_link_page",
    "service_unavailable_page",
]
