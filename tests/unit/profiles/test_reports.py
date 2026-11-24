"""Newest run report of a profile and its error extraction."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.unit.common.profile_helpers import write_run_report

from content_factory.schemas.generator.run import RunStatus
from content_factory.schemas.profiles.registry import ProfileInfo
from content_factory.services.profiles.reports import latest_run


def _profile(tmp_path: Path) -> ProfileInfo:
    return ProfileInfo(
        id="dating/profile_1",
        category="dating",
        name="profile_1",
        language="ru",
        niche="dating",
        width=544,
        height=960,
        audio=True,
        dir=tmp_path / "profiles" / "dating" / "profile_1",
    )


def _failed_item(error: str | None) -> dict[str, Any]:
    return {
        "file": "001.mp4",
        "profile": "dating/profile_1",
        "look_id": "look-1",
        "seed": 1,
        "frames": 10,
        "status": "failed",
        "error": error,
    }


def test_latest_run_is_null_without_reports(tmp_path: Path) -> None:
    assert latest_run(tmp_path / "generated", _profile(tmp_path)) is None


def test_latest_run_returns_newest_by_finished_at(tmp_path: Path) -> None:
    output = tmp_path / "generated"
    write_run_report(
        output,
        run_id="old",
        finished_at=datetime(2026, 5, 1, 12, 30, tzinfo=UTC),
        generated=1,
    )
    write_run_report(
        output,
        run_id="new",
        finished_at=datetime(2026, 5, 1, 13, 30, tzinfo=UTC),
        generated=3,
        failed=1,
        items=[_failed_item("сломался рендер")],
    )

    report = latest_run(output, _profile(tmp_path))

    assert report is not None
    assert report.status == RunStatus.DONE
    assert report.finished_at == datetime(2026, 5, 1, 13, 30, tzinfo=UTC)
    assert report.generated == 3
    assert report.failed == 1
    assert report.error == "сломался рендер"


def test_latest_run_uses_started_at_for_running_report(tmp_path: Path) -> None:
    output = tmp_path / "generated"
    write_run_report(
        output,
        run_id="done",
        started_at=datetime(2026, 5, 1, 10, 0, tzinfo=UTC),
        finished_at=datetime(2026, 5, 1, 11, 0, tzinfo=UTC),
    )
    write_run_report(
        output,
        run_id="running",
        status="running",
        started_at=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
        finished_at=None,
    )

    report = latest_run(output, _profile(tmp_path))

    assert report is not None
    assert report.status == RunStatus.RUNNING
    assert report.finished_at is None


def test_latest_run_skips_broken_report_with_warning(
    tmp_path: Path,
    log_records: list[dict[str, Any]],
) -> None:
    output = tmp_path / "generated"
    write_run_report(output, run_id="good")
    broken = output / "ru" / "dating_profile_1" / "broken" / "run.json"
    broken.parent.mkdir(parents=True)
    broken.write_text("{not json", encoding="utf-8")

    report = latest_run(output, _profile(tmp_path))

    assert report is not None
    assert report.generated == 0
    records = [record for record in log_records if record["function"] == "_read"]
    assert records[-1]["level"].name == "WARNING"
    assert records[-1]["extra"]["path"] == str(broken)


def test_latest_run_skips_invalid_report_schema(
    tmp_path: Path,
    log_records: list[dict[str, Any]],
) -> None:
    output = tmp_path / "generated"
    invalid = output / "ru" / "dating_profile_1" / "invalid" / "run.json"
    invalid.parent.mkdir(parents=True)
    invalid.write_text('{"run_id": "x"}', encoding="utf-8")

    assert latest_run(output, _profile(tmp_path)) is None
    assert any(record["function"] == "_read" for record in log_records)


def test_latest_run_skips_unreadable_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    log_records: list[dict[str, Any]],
) -> None:
    output = tmp_path / "generated"
    report = write_run_report(output)
    original_read_text = Path.read_text

    def _deny(self: Path, *args: object, **kwargs: object) -> str:
        if self == report:
            raise OSError("permission denied")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _deny)

    assert latest_run(output, _profile(tmp_path)) is None
    records = [record for record in log_records if record["function"] == "_read"]
    assert records[-1]["level"].name == "WARNING"


def test_latest_run_truncates_first_failed_item_error(tmp_path: Path) -> None:
    long_error = "я" * 400
    write_run_report(
        tmp_path / "generated",
        failed=2,
        items=[
            _failed_item(None),
            _failed_item(long_error),
            _failed_item("второй"),
        ],
    )

    report = latest_run(tmp_path / "generated", _profile(tmp_path))

    assert report is not None
    assert report.error is not None
    assert len(report.error) == 300
    assert report.error == "я" * 300


def test_latest_run_returns_no_error_for_successful_items(tmp_path: Path) -> None:
    write_run_report(
        tmp_path / "generated",
        generated=1,
        items=[
            {
                "file": "001.mp4",
                "profile": "dating/profile_1",
                "look_id": "look-1",
                "seed": 1,
                "frames": 10,
                "status": "done",
            }
        ],
    )

    report = latest_run(tmp_path / "generated", _profile(tmp_path))

    assert report is not None
    assert report.error is None


def test_latest_run_logs_directory_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    log_records: list[dict[str, Any]],
) -> None:
    directory = tmp_path / "generated" / "ru" / "dating_profile_1"
    directory.mkdir(parents=True)
    original_glob = Path.glob

    def _deny(self: Path, pattern: str) -> object:
        if self == directory:
            raise PermissionError("permission denied")
        return original_glob(self, pattern)

    monkeypatch.setattr(Path, "glob", _deny)

    assert latest_run(tmp_path / "generated", _profile(tmp_path)) is None
    records = [record for record in log_records if record["function"] == "latest_run"]
    assert records[-1]["level"].name == "WARNING"
    assert records[-1]["extra"]["path"] == str(directory)
