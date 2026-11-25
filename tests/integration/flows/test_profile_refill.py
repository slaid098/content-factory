"""Scheduled profile refill flow: defaults, stock counting and outcomes."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from content_factory.db.connection import connect
from content_factory.db.lock import GENERATOR_LOCK, lock_path, try_acquire
from content_factory.db.profiles import upsert_refill
from content_factory.flows.profile_refill.flow import profile_refill
from content_factory.schemas.profiles.refill import ProfileRefill

_NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC).isoformat()


@pytest.fixture(autouse=True)
def _flow_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the flow at an isolated data directory."""
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))


def _make_profile(
    root: Path,
    category: str = "dating",
    name: str = "profile_2",
) -> None:
    directory = root / "profiles" / category / name
    directory.mkdir(parents=True)
    data = {
        "language": "ru",
        "niche": category,
        "width": 512,
        "height": 896,
        "audio": True,
    }
    (directory / "profile.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True),
        encoding="utf-8",
    )


def _set_refill(root: Path, profile_id: str, refill: ProfileRefill) -> None:
    connection = connect(root / "db" / "db.sqlite3")
    try:
        upsert_refill(connection, profile_id, refill, _NOW)
        connection.commit()
    finally:
        connection.close()


def _touch_stock(root: Path, counts: int, category: str = "dating") -> None:
    directory = root / "content" / category / "ru"
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(counts):
        (directory / f"dating_profile_2_run_{index:03}.mp4").write_bytes(b"video")


def _recorder(calls: list[tuple[str, int]]) -> Callable[[str, int], None]:
    def start(profile_id: str, count: int) -> None:
        calls.append((profile_id, count))

    return start


def test_flow_is_callable_and_named() -> None:
    assert callable(profile_refill)
    assert profile_refill.name == "profile_refill"


def test_all_profiles_disabled_start_nothing(tmp_path: Path) -> None:
    _make_profile(tmp_path)
    calls: list[tuple[str, int]] = []

    outcome = profile_refill.fn(starter=_recorder(calls))

    assert outcome == "disabled"
    assert calls == []


def test_enabled_profile_starts_exactly_one_batch(tmp_path: Path) -> None:
    _make_profile(tmp_path)
    _set_refill(tmp_path, "dating/profile_2", ProfileRefill(True, 3, 1))
    calls: list[tuple[str, int]] = []

    outcome = profile_refill.fn(starter=_recorder(calls))

    assert outcome == "started:dating/profile_2"
    assert calls == [("dating/profile_2", 3)]


def test_only_first_profile_by_id_is_started(tmp_path: Path) -> None:
    _make_profile(tmp_path, "z", "profile_1")
    _make_profile(tmp_path, "a", "profile_1")
    _set_refill(tmp_path, "z/profile_1", ProfileRefill(True, 2, 0))
    _set_refill(tmp_path, "a/profile_1", ProfileRefill(True, 4, 0))
    calls: list[tuple[str, int]] = []

    outcome = profile_refill.fn(starter=_recorder(calls))

    assert outcome == "started:a/profile_1"
    assert calls == [("a/profile_1", 4)]


def test_profile_with_enough_stock_is_idle(tmp_path: Path) -> None:
    _make_profile(tmp_path)
    _set_refill(tmp_path, "dating/profile_2", ProfileRefill(True, 3, 2))
    _touch_stock(tmp_path, 3)
    calls: list[tuple[str, int]] = []

    outcome = profile_refill.fn(starter=_recorder(calls))

    assert outcome == "idle"
    assert calls == []


def test_profile_at_threshold_refills_only_the_deficit(tmp_path: Path) -> None:
    _make_profile(tmp_path)
    _set_refill(tmp_path, "dating/profile_2", ProfileRefill(True, 3, 2))
    _touch_stock(tmp_path, 2)
    calls: list[tuple[str, int]] = []

    outcome = profile_refill.fn(starter=_recorder(calls))

    assert outcome == "started:dating/profile_2"
    assert calls == [("dating/profile_2", 1)]


def test_zero_threshold_with_empty_stock_uses_global_target(tmp_path: Path) -> None:
    _make_profile(tmp_path)
    _set_refill(tmp_path, "dating/profile_2", ProfileRefill(True, None, 0))
    calls: list[tuple[str, int]] = []

    outcome = profile_refill.fn(starter=_recorder(calls))

    assert outcome == "started:dating/profile_2"
    assert calls == [("dating/profile_2", 20)]


def test_busy_generator_skips_the_tick(tmp_path: Path) -> None:
    _make_profile(tmp_path)
    _set_refill(tmp_path, "dating/profile_2", ProfileRefill(True, 2, 0))
    lock = try_acquire(lock_path(tmp_path / "db", GENERATOR_LOCK))
    assert lock is not None
    calls: list[tuple[str, int]] = []
    try:
        outcome = profile_refill.fn(starter=_recorder(calls))
    finally:
        lock.release()

    assert outcome == "generator_busy"
    assert calls == []


def test_starter_failure_returns_start_failed(
    tmp_path: Path,
    log_records: list[dict[str, Any]],
) -> None:
    _make_profile(tmp_path)
    _set_refill(tmp_path, "dating/profile_2", ProfileRefill(True, 2, 0))

    def failing(profile_id: str, count: int) -> None:
        raise RuntimeError("prefect down")

    outcome = profile_refill.fn(starter=failing)

    assert outcome == "start_failed"
    assert any(
        record["level"].name == "ERROR" and "prefect down" in record["message"]
        for record in log_records
    )


def test_profile_without_settings_row_is_ignored(tmp_path: Path) -> None:
    _make_profile(tmp_path)
    _set_refill(tmp_path, "deleted/profile_1", ProfileRefill(True, 2, 0))
    calls: list[tuple[str, int]] = []

    outcome = profile_refill.fn(starter=_recorder(calls))

    assert outcome == "disabled"
    assert calls == []
