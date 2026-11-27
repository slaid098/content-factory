"""Flow: unfinished generator runs are resumed before a new batch."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tests.unit.generator.helpers import make_profile

from content_factory.constants import DB_DIR, DB_FILENAME
from content_factory.db import generator_runs
from content_factory.db.connection import connect
from content_factory.flows.generate_batch import flow as flow_module
from content_factory.flows.generate_batch.flow import generate_batch
from content_factory.schemas.generator.journal import JournalPlanItem
from content_factory.schemas.generator.run import RunReport

_RUN_ID = "20260101T000000Z"
_PROFILE = "dating/profile_2"
_SLUG = "dating_profile_2"
_FILE = f"{_SLUG}_{_RUN_ID}_001.mp4"
_NOW = "2026-01-01T00:00:00+00:00"

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


class _Provider:
    """Vast.ai account fake that records any rent attempt."""

    def __init__(self, token: str = "") -> None:
        self.token = token
        self.queries = 0
        self.created: list[dict[str, Any]] = []

    def list_instances(self) -> list[dict[str, Any]]:
        self.queries += 1
        return []

    def create_instance(self, config: dict[str, Any]) -> str:
        self.created.append(dict(config))
        raise AssertionError("resume must not rent")

    def search_offers(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        del query
        raise AssertionError("resume must not rent")


@pytest.fixture(autouse=True)
def _flow_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the flow at an isolated data directory."""
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GENERATOR_OUTPUT_DIR", str(tmp_path / "generated"))
    monkeypatch.setattr(flow_module, "VastProvider", _Provider)


def _db_path(tmp_path: Path) -> Path:
    return tmp_path / DB_DIR / DB_FILENAME


def _seed_run(
    tmp_path: Path,
    *,
    status: str = "done",
    profile: str = _PROFILE,
    file: str = _FILE,
) -> None:
    connection = connect(_db_path(tmp_path))
    try:
        generator_runs.insert_run(
            connection,
            run_id=_RUN_ID,
            profiles=[profile],
            count=1,
            content_type="video",
            now=_NOW,
        )
        generator_runs.insert_plan(
            connection,
            _RUN_ID,
            [
                JournalPlanItem(
                    profile=profile,
                    file=file,
                    producer="vast",
                    type="video",
                    look_id="c01",
                    seed=7,
                    frames=119,
                )
            ],
            _NOW,
        )
        if status == "done":
            generator_runs.mark_published(
                connection,
                run_id=_RUN_ID,
                profile=profile,
                file=file,
                now=_NOW,
            )
        connection.commit()
    finally:
        connection.close()


def _run_row(tmp_path: Path) -> generator_runs.GeneratorRunRow:
    connection = connect(_db_path(tmp_path))
    try:
        row = generator_runs.get_run(connection, _RUN_ID)
    finally:
        connection.close()
    assert row is not None
    return row


def _items(tmp_path: Path) -> list[generator_runs.GeneratorItemRow]:
    connection = connect(_db_path(tmp_path))
    try:
        return generator_runs.list_items(connection, _RUN_ID)
    finally:
        connection.close()


def _stub_new_group(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[str]:
    """Replace the new batch with a recorder that reads the old run status."""
    seen: list[str] = []

    def fake_run_group(*args: Any, **kwargs: Any) -> tuple[str, int, int]:
        del args, kwargs
        seen.append(_run_row(tmp_path).status)
        return "new-run", 0, 0

    monkeypatch.setattr(flow_module, "_run_group", fake_run_group)
    return seen


def test_generate_batch_resumes_an_unfinished_run_before_a_new_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_profile(tmp_path / "profiles")
    _seed_run(tmp_path, status="done")
    seen = _stub_new_group(monkeypatch, tmp_path)

    outcome = generate_batch.fn(profiles=_PROFILE, count=1)

    assert "run=new-run" in outcome
    assert seen == ["done"]
    assert _run_row(tmp_path).status == "done"
    report_path = tmp_path / "generated" / "ru" / _SLUG / _RUN_ID / "run.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert set(report) == set(RunReport.model_fields)
    assert report["run_id"] == _RUN_ID
    assert [item["file"] for item in report["items"]] == [_FILE]
    assert [item["status"] for item in report["items"]] == ["done"]


def test_dry_run_neither_journals_nor_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_profile(tmp_path / "profiles")
    _seed_run(tmp_path, status="running")
    seen = _stub_new_group(monkeypatch, tmp_path)

    outcome = generate_batch.fn(profiles=_PROFILE, count=1, dry_run=True)

    assert "status=dry_run" in outcome
    assert seen == ["running"]
    assert _run_row(tmp_path).status == "running"
    assert _items(tmp_path)[0].status == "planned"


def test_a_run_that_cannot_be_resumed_fails_and_the_new_batch_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    make_profile(tmp_path / "profiles")
    _seed_run(tmp_path, status="running", profile="dating/gone")
    seen = _stub_new_group(monkeypatch, tmp_path)

    outcome = generate_batch.fn(profiles=_PROFILE, count=1)

    assert "run=new-run" in outcome
    assert seen == ["failed"]
    row = _run_row(tmp_path)
    assert row.status == "failed"
    assert row.error is not None and "профиль не найден" in row.error
    assert [item.status for item in _items(tmp_path)] == ["failed"]
