"""``run.json`` report of one generator batch: build, count and write."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from content_factory.schemas.generator.run import (
    RunItem,
    RunReport,
    RunStatus,
    RunTotals,
)
from content_factory.services.generator.plan import _PlannedItem
from content_factory.services.generator.profiles import _Profile

_RUN_FILENAME = "run.json"
_FINAL_DIR = "final"


def _report(
    profile: _Profile,
    items: tuple[_PlannedItem, ...],
    run_id: str,
    started: datetime,
    count: int,
    dry_run: bool,
) -> RunReport:
    """Build the initial ``run.json`` report of one profile."""
    status = RunStatus.DRY_RUN if dry_run else RunStatus.RUNNING
    return RunReport(
        run_id=run_id,
        started_at=started,
        status=status,
        profiles=[profile.name],
        count=count,
        items=[
            RunItem(
                file=item.filename,
                profile=profile.name,
                look_id=item.look.id,
                seed=item.seed,
                frames=item.frames,
                status=status,
            )
            for item in items
        ],
    )


def _finalize(report: RunReport, run_dir: Path, items: tuple[_PlannedItem, ...]) -> None:
    """Count results and write the final report."""
    generated = sum(1 for item in report.items if item.status == RunStatus.DONE)
    failed = sum(1 for item in report.items if item.status == RunStatus.FAILED)
    size = sum(
        (run_dir / _FINAL_DIR / item.filename).stat().st_size
        for item in items
        if (run_dir / _FINAL_DIR / item.filename).is_file()
    )
    report.totals = RunTotals(generated=generated, failed=failed, bytes=size)
    report.status = RunStatus.DONE if failed == 0 else RunStatus.FAILED
    report.finished_at = datetime.now(UTC)
    _write_report(run_dir, report)


def _write_report(run_dir: Path, report: RunReport) -> None:
    """Atomically write ``run.json`` into the run directory."""
    run_dir.mkdir(parents=True, exist_ok=True)
    target = run_dir / _RUN_FILENAME
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def _status(generated: int, failed: int) -> str:
    """Return the final batch status."""
    return RunStatus.FAILED.value if failed else RunStatus.DONE.value
