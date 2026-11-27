"""Rental factory that binds every Vast.ai session to the server journal."""

from __future__ import annotations

from pathlib import Path

from content_factory.config.settings import GeneratorSettings
from content_factory.constants import DB_DIR, DB_FILENAME
from content_factory.db.server_journal import ServerJournal
from content_factory.services.generator.comfy import _SessionFactory
from content_factory.services.generator.profiles import _Profile
from content_factory.services.generator.vast import VastSession


def _production_sessions(data_root: Path, run_id: str) -> _SessionFactory:
    """Build the rental factory that journals every Vast.ai server."""
    journal = ServerJournal(data_root / DB_DIR / DB_FILENAME)

    def create(profile: _Profile, settings: GeneratorSettings) -> VastSession:
        return VastSession(profile, settings, journal=journal, run_id=run_id)

    return create


__all__ = ["_production_sessions"]
