"""ASGI entry point."""

from __future__ import annotations

from content_factory.api.app import create_app
from content_factory.config.access_log import install_access_log_filter
from content_factory.config.logger import configure_logger
from content_factory.config.settings import load_settings

settings = load_settings()
configure_logger(settings.log_level)
install_access_log_filter()

app = create_app()
