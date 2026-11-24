"""Newest ``run.json`` report of one generator profile."""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from pydantic import ValidationError

from content_factory.schemas.generator.run import RunReport, RunStatus
from content_factory.schemas.profiles.api import ProfileLastRun
from content_factory.schemas.profiles.registry import ProfileInfo

_ERROR_LIMIT = 300


def latest_run(output_dir: Path, profile: ProfileInfo) -> ProfileLastRun | None:
    """Read the newest batch report of *profile*.

    Reports live under ``<output_dir>/<language>/<category>_<name>/<run>/``.
    A unreadable or malformed file is skipped with a warning; a profile
    without a readable report yields ``None``.

    Args:
        output_dir: ``generator.output_dir`` of the generator settings.
        profile: Registry fields of the profile.

    Returns:
        Status, counters and the first failure of the newest finished batch.
    """
    directory = output_dir / profile.language / f"{profile.category}_{profile.name}"
    try:
        paths = sorted(directory.glob("*/run.json"))
    except OSError as ex:
        logger.bind(path=str(directory), error=ex, error_type=type(ex).__name__).warning(
            "Не удалось прочитать каталог прогонов профиля {}",
            profile.id,
        )
        return None
    reports: list[tuple[Path, RunReport]] = []
    for path in paths:
        report = _read(path)
        if report is not None:
            reports.append((path, report))
    if not reports:
        return None
    _, newest = max(reports, key=lambda pair: (_moment(pair[1]), pair[0].as_posix()))
    return ProfileLastRun(
        status=newest.status,
        finished_at=newest.finished_at,
        generated=newest.totals.generated,
        failed=newest.totals.failed,
        error=_first_error(newest),
    )


def _read(path: Path) -> RunReport | None:
    """Parse one report, skipping a broken file with a warning."""
    try:
        return RunReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as ex:
        logger.bind(path=str(path), error=ex, error_type=type(ex).__name__).warning(
            "Пропущен битый run.json {}",
            path,
        )
        return None


def _moment(report: RunReport) -> float:
    """Return the ordering moment of a report in seconds."""
    moment = report.finished_at or report.started_at
    try:
        return moment.timestamp()
    except (OSError, OverflowError, ValueError):
        return 0.0


def _first_error(report: RunReport) -> str | None:
    """Return the first error of a failed item, truncated for the API."""
    for item in report.items:
        if item.status == RunStatus.FAILED and item.error:
            return item.error[:_ERROR_LIMIT]
    return None


__all__ = ["latest_run"]
