"""Small report helpers shared by the profile producers.

The helpers only update the ``run.json`` model; they never touch the source
pool or the stock, so both the photo and the unique video producer can use
them without owning each other's lifecycle.
"""

from __future__ import annotations

from datetime import UTC, datetime

from loguru import logger

from content_factory.exceptions.generator import GeneratorError
from content_factory.schemas.content.delivery import ContentType
from content_factory.schemas.generator.run import (
    ProductionStep,
    RunItem,
    RunReport,
    RunStatus,
    RunTotals,
)


def close_report(report: RunReport, generated: int, failed: int, total_bytes: int) -> None:
    """Store the counted outcome and the finish moment in the report."""
    report.totals = RunTotals(generated=generated, failed=failed, bytes=total_bytes)
    report.status = RunStatus.DONE if failed == 0 else RunStatus.FAILED
    report.finished_at = datetime.now(UTC)


def mark_failed(item: RunItem, error: Exception, message: str) -> None:
    """Mark one item failed and log the reason without stopping the run."""
    item.status = RunStatus.FAILED
    item.error = str(error)
    logger.bind(file=item.file, error=str(error), error_type=type(error).__name__).error(message)


def find_step(
    steps: tuple[ProductionStep, ...],
    producer: str,
    content_type: ContentType,
) -> ProductionStep:
    """Return the recipe step of one producer and content type.

    Raises:
        GeneratorError: The recipe has no such step.
    """
    for step in steps:
        if step.producer == producer and step.type is content_type:
            return step
    raise GeneratorError(f"в рецепте профиля нет шага {producer}/{content_type.value}")


__all__ = ["close_report", "find_step", "mark_failed"]
