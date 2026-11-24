"""Prefect flow: dry run and the single-run generator lock."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.unit.generator.helpers import make_profile

from content_factory.db.lock import GENERATOR_LOCK, lock_path, try_acquire
from content_factory.exceptions.generator import GeneratorBusyError
from content_factory.flows.generate_batch.flow import generate_batch

_ENV_NAMES = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "VAST_API_TOKEN",
    "GENERATOR_MODEL",
    "GENERATOR_MAX_COST_PER_HOUR",
    "GENERATOR_VIDEOS_PER_SERVER",
    "GENERATOR_MAX_SERVERS",
    "GENERATOR_OUTPUT_DIR",
)


@pytest.fixture(autouse=True)
def _flow_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the flow at an isolated data directory."""
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GENERATOR_OUTPUT_DIR", str(tmp_path / "generated"))


def test_flow_is_callable_and_named() -> None:
    assert callable(generate_batch)
    assert generate_batch.name == "generate_batch"


def test_dry_run_writes_report_without_network(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles")

    outcome = generate_batch.fn(profiles="dating/profile_2", count=2, dry_run=True)

    assert "status=dry_run" in outcome
    assert "generated=0" in outcome
    reports = list((tmp_path / "generated").rglob("run.json"))
    assert len(reports) == 1
    assert '"status": "dry_run"' in reports[0].read_text(encoding="utf-8")
    assert not list((tmp_path / "generated").rglob("rotation.json"))


def test_dry_run_keeps_all_split_videos_in_plan_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GENERATOR_VIDEOS_PER_SERVER", "7")
    monkeypatch.setenv("GENERATOR_MAX_SERVERS", "4")
    make_profile(tmp_path / "profiles")

    outcome = generate_batch.fn(profiles="dating/profile_2", count=14, dry_run=True)

    assert "status=dry_run" in outcome
    report_path = next((tmp_path / "generated").rglob("run.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert [item["file"] for item in report["items"]] == [
        f"dating_profile_2_{report['run_id']}_{seq:03d}.mp4" for seq in range(1, 15)
    ]
    assert [item["status"] for item in report["items"]] == ["dry_run"] * 14


def test_second_run_is_rejected_while_lock_is_held(tmp_path: Path) -> None:
    lock = try_acquire(lock_path(tmp_path / "db", GENERATOR_LOCK))
    assert lock is not None
    try:
        with pytest.raises(GeneratorBusyError, match="уже запущен"):
            generate_batch.fn(profiles="dating/profile_2", dry_run=True)
    finally:
        lock.release()
