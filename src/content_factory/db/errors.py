"""Database errors of the connection port.

The types are declared in :mod:`content_factory.exceptions.errors` with the
other service errors and re-exported here, so the database layer imports its
errors from one place next to the adapters.
"""

from __future__ import annotations

from content_factory.exceptions.errors import (
    DatabaseError,
    IntegrityError,
    OperationalError,
)

__all__ = ["DatabaseError", "IntegrityError", "OperationalError"]
