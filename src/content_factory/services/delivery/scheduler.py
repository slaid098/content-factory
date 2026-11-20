"""Hourly cleanup of expired delivery archives."""

from __future__ import annotations

from datetime import UTC
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from content_factory.services.delivery.service import cleanup_expired_archives

ARCHIVE_CLEANUP_JOB_ID = "delivery-archive-cleanup"


def create_cleanup_scheduler(data_dir: Path, retention_hours: int) -> AsyncIOScheduler:
    """Build the scheduler that removes expired archives every hour.

    Args:
        data_dir: Root of the runtime data directory.
        retention_hours: Archive lifetime passed to the cleanup function.

    Returns:
        A configured scheduler; the caller starts and stops it with the app.
    """
    scheduler = AsyncIOScheduler(timezone=UTC)
    scheduler.add_job(
        cleanup_expired_archives,
        trigger=CronTrigger(minute=0, timezone=UTC),
        args=[data_dir, retention_hours],
        id=ARCHIVE_CLEANUP_JOB_ID,
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    return scheduler


def run_startup_cleanup(data_dir: Path, retention_hours: int) -> None:
    """Remove expired archives once when the API starts."""
    cleanup_expired_archives(data_dir, retention_hours)
