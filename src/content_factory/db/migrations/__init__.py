"""Forward-only schema migrations of the operational database."""

from __future__ import annotations

MIGRATION_MODULES = (
    "content_factory.db.migrations.0001_initial",
    "content_factory.db.migrations.0002_sources",
)

__all__ = ["MIGRATION_MODULES"]
