"""Prefect flow: dry run and the single-run generator lock."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.unit.generator.helpers import make_profile

from content_factory.db.lock import GENERATOR_LOCK, lock_path, try_acquire
from content_factory.exceptions.generator import GeneratorBusyError
from content_factory.flows.generate_batch.flow import generate_batch

_ENV_NAMES = (
    "VAST_API_TOKEN",
    "GENERATOR_AI_KEY",
    "GENERATOR_AI_URL",
    "GENERATOR_AI_MODEL",
    "GENERATOR_MAX_COST_PER_HOUR",
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


def test_second_run_is_rejected_while_lock_is_held(tmp_path: Path) -> None:
    lock = try_acquire(lock_path(tmp_path / "db", GENERATOR_LOCK))
    assert lock is not None
    try:
        with pytest.raises(GeneratorBusyError, match="уже запущен"):
            generate_batch.fn(profiles="dating/profile_2", dry_run=True)
    finally:
        lock.release()
