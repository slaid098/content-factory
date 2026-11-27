"""Forward-only schema migrations of the operational database."""

from __future__ import annotations

MIGRATION_MODULES = (
    "content_factory.db.migrations.0001_initial",
    "content_factory.db.migrations.0002_sources",
    "content_factory.db.migrations.0003_photo_refill",
    "content_factory.db.migrations.0004_trusted_devices",
    "content_factory.db.migrations.0005_generator_servers",
)

__all__ = ["MIGRATION_MODULES"]
