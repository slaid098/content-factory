"""Content delivery service capsule."""

from content_factory.services.delivery.service import cleanup_expired_archives, deliver

__all__ = ["cleanup_expired_archives", "deliver"]
