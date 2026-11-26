"""Uvicorn access-log redaction of one-time join links.

The production image keeps the access log on, and the raw ``/join/<token>``
token must not reach it: the URL is a credential until the first browser opens
it. :func:`install_access_log_filter` attaches :class:`JoinTokenFilter` to
``uvicorn.access`` so the token becomes ``/join/[redacted]`` while every other
path, the HTTP method, status and client address stay intact.

The filter is installed by the ASGI entry point, not by the library, so tests
and one-off scripts that build the app never mutate the global logging config.
"""

from __future__ import annotations

import logging
from typing import Final

_ACCESS_LOGGER: Final = "uvicorn.access"
_JOIN_PREFIX: Final = "/join/"
_REDACTED_JOIN_PATH: Final = "/join/[redacted]"


def redact_join_path(path: str) -> str:
    """Return *path* with a one-time join token removed.

    Only the ``/join/<token>`` request target is rewritten; ``/join`` itself
    and every other path stay byte-for-byte identical. The query string is
    dropped together with the token because a link may carry extra parameters
    the access log has no reason to keep.
    """
    if not path.startswith(_JOIN_PREFIX):
        return path
    return _REDACTED_JOIN_PATH


class JoinTokenFilter(logging.Filter):
    """Sanitize the request path of a uvicorn access ``LogRecord``.

    Uvicorn formats access records from a five-item ``args`` tuple
    ``(client, method, path, http_version, status)``. A record with another
    ``args`` shape, a non-string path or a path outside ``/join/`` is passed
    through unchanged.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 3:
            return True
        path = args[2]
        if not isinstance(path, str):
            return True
        redacted = redact_join_path(path)
        if redacted == path:
            return True
        record.args = (args[0], args[1], redacted, *args[3:])
        return True


def install_access_log_filter() -> None:
    """Attach :class:`JoinTokenFilter` to ``uvicorn.access`` exactly once."""
    access_logger = logging.getLogger(_ACCESS_LOGGER)
    if any(isinstance(item, JoinTokenFilter) for item in access_logger.filters):
        return
    access_logger.addFilter(JoinTokenFilter())


__all__ = [
    "JoinTokenFilter",
    "install_access_log_filter",
    "redact_join_path",
]
