"""Recovery of orphaned Vast.ai generator servers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.unit.generator.helpers import make_profile

from content_factory.db import generator_runs, generator_servers
from content_factory.db.connection import connect
from content_factory.db.errors import DatabaseError
from content_factory.db.lock import GENERATOR_LOCK, lock_path, try_acquire
from content_factory.exceptions.generator import RentError
from content_factory.flows.generator_recovery import flow as flow_module
from content_factory.flows.generator_recovery import recovery as recovery_module
from content_factory.flows.generator_recovery.flow import generator_recovery
from content_factory.schemas.generator.journal import JournalPlanItem
from content_factory.services.generator.vast import (
    GENERATOR_LABEL,
    destroy_verified,
)
from content_factory.services.generator.vast_cli import VastProvider

_NOW = 1_800_000_000.0
_RUN_ID = "20260101T000000Z"


@pytest.fixture(autouse=True)
def _flow_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the flow at an isolated data directory and a fixed clock."""
    monkeypatch.setenv("CONTENT_FACTORY_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("GENERATOR_OUTPUT_DIR", str(tmp_path / "generated"))
    monkeypatch.setattr(flow_module, "time", lambda: _NOW)


def _iso(seconds: float) -> str:
    return datetime.fromtimestamp(seconds, UTC).isoformat()


def _db(tmp_path: Path):
    return connect(tmp_path / "db" / "db.sqlite3")


def _insert_renting(
    tmp_path: Path,
    *,
    server_id: str = "s1",
    minutes_old: float = 0,
    contract_id: str | None = "c1",
    ready: bool = False,
    run_id: str = _RUN_ID,
) -> str:
    """Insert one journal row as if the rental started *minutes_old* ago."""
    created = _iso(_NOW - minutes_old * 60)
    connection = _db(tmp_path)
    try:
        generator_servers.insert_renting(
            connection,
            server_id=server_id,
            run_id=run_id,
            profile="dating/profile_1",
            gpu="L40S",
            image="img",
            cost_per_hour=0.4,
            now=created,
        )
        if contract_id is not None:
            generator_servers.bind_contract(connection, server_id, contract_id, created)
        if ready:
            generator_servers.mark_ready(
                connection,
                server_id,
                ip="1.2.3.4",
                port=40000,
                gpu="L40S",
                image="img",
                cost_per_hour=0.4,
                now=created,
            )
        connection.commit()
    finally:
        connection.close()
    return server_id


def _row(tmp_path: Path, server_id: str = "s1") -> generator_servers.GeneratorServerRow:
    connection = _db(tmp_path)
    try:
        row = generator_servers.get_row(connection, server_id)
    finally:
        connection.close()
    assert row is not None
    return row


def _instance(
    contract_id: str = "c1",
    *,
    age_minutes: float = 30,
    label: str = GENERATOR_LABEL,
    start_date: bool = True,
) -> dict[str, Any]:
    instance: dict[str, Any] = {"id": contract_id, "label": label}
    if start_date:
        instance["start_date"] = _NOW - age_minutes * 60
    return instance


class _Provider:
    """In-memory Vast.ai account with verified teardown semantics."""

    def __init__(self, instances: list[dict[str, Any]] | None = None) -> None:
        self.instances = {str(instance["id"]): dict(instance) for instance in instances or []}
        self.queries = 0
        self.destroyed: list[str] = []

    def list_instances(self) -> list[dict[str, Any]]:
        self.queries += 1
        return [dict(instance) for instance in self.instances.values()]

    def destroy_instance(self, contract_id: str) -> bool:
        self.destroyed.append(str(contract_id))
        self.instances.pop(str(contract_id), None)
        return True

    def show_instance(self, contract_id: str) -> dict[str, Any]:
        instance = self.instances.get(str(contract_id))
        if instance is None:
            raise RentError(f"Instance {contract_id} not found")
        return dict(instance)

    def search_offers(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return []

    def create_instance(self, config: dict[str, Any]) -> str:
        raise AssertionError("recovery must not rent")


def _run(provider: _Provider) -> str:
    return generator_recovery.fn(provider=provider)  # type: ignore[arg-type]


def test_flow_is_callable_and_named() -> None:
    assert callable(generator_recovery)
    assert generator_recovery.name == "generator_recovery"


def test_busy_generator_lock_returns_busy_without_vast_calls(
    tmp_path: Path,
) -> None:
    lock = try_acquire(lock_path(tmp_path / "db", GENERATOR_LOCK))
    assert lock is not None
    provider = _Provider([_instance()])
    try:
        outcome = _run(provider)
    finally:
        lock.release()

    assert outcome == "busy"
    assert provider.queries == 0
    assert provider.destroyed == []


def test_unbound_labelled_instance_older_than_grace_is_destroyed(
    tmp_path: Path,
) -> None:
    provider = _Provider([_instance(age_minutes=11)])

    outcome = _run(provider)  # type: ignore[arg-type]

    assert outcome == "destroyed=1 lost=0 failed=0"
    assert provider.destroyed == ["c1"]
    assert provider.instances == {}


def test_young_foreign_and_unlabelled_instances_are_kept(tmp_path: Path) -> None:
    provider = _Provider(
        [
            _instance("young", age_minutes=5),
            _instance("foreign", age_minutes=60, label="someone-else"),
            _instance("bare", age_minutes=60, label=""),
        ]
    )

    outcome = _run(provider)  # type: ignore[arg-type]

    assert outcome == "destroyed=0 lost=0 failed=0"
    assert provider.destroyed == []
    assert set(provider.instances) == {"young", "foreign", "bare"}


def test_instance_without_start_date_is_not_destroyed(tmp_path: Path) -> None:
    provider = _Provider([_instance(age_minutes=60, start_date=False)])

    outcome = _run(provider)  # type: ignore[arg-type]

    assert outcome == "destroyed=0 lost=0 failed=0"
    assert provider.destroyed == []
    assert set(provider.instances) == {"c1"}


def test_bound_orphan_row_is_destroyed_and_closed(tmp_path: Path) -> None:
    _insert_renting(tmp_path, minutes_old=11, ready=True)
    provider = _Provider([_instance(age_minutes=11)])

    outcome = _run(provider)  # type: ignore[arg-type]

    assert outcome == "destroyed=1 lost=0 failed=0"
    assert provider.destroyed == ["c1"]
    row = _row(tmp_path)
    assert row.status == generator_servers.DESTROYED
    assert row.destroyed_at is not None


def test_fresh_bound_rental_is_kept(tmp_path: Path) -> None:
    _insert_renting(tmp_path, minutes_old=5, ready=True)
    provider = _Provider([_instance(age_minutes=5)])

    outcome = _run(provider)  # type: ignore[arg-type]

    assert outcome == "destroyed=0 lost=0 failed=0"
    assert provider.destroyed == []
    assert _row(tmp_path).status == generator_servers.READY


def test_row_older_than_ttl_is_destroyed_in_the_same_pass(tmp_path: Path) -> None:
    _insert_renting(tmp_path, minutes_old=7 * 60, ready=True)
    provider = _Provider([_instance(age_minutes=1)])

    outcome = _run(provider)  # type: ignore[arg-type]

    assert outcome == "destroyed=1 lost=0 failed=0"
    assert provider.destroyed == ["c1"]


def test_vanished_contract_marks_the_row_lost(tmp_path: Path) -> None:
    _insert_renting(tmp_path, minutes_old=11, ready=True)
    provider = _Provider()

    outcome = _run(provider)  # type: ignore[arg-type]

    assert outcome == "destroyed=0 lost=1 failed=0"
    row = _row(tmp_path)
    assert row.status == generator_servers.LOST
    assert row.last_error is not None


def test_contractless_renting_row_becomes_lost_only_after_grace(
    tmp_path: Path,
) -> None:
    _insert_renting(tmp_path, server_id="old", minutes_old=11, contract_id=None)
    _insert_renting(tmp_path, server_id="fresh", minutes_old=5, contract_id=None)
    provider = _Provider()

    outcome = _run(provider)  # type: ignore[arg-type]

    assert outcome == "destroyed=0 lost=1 failed=0"
    assert _row(tmp_path, "old").status == generator_servers.LOST
    assert _row(tmp_path, "fresh").status == generator_servers.RENTING


def test_failed_teardown_keeps_the_row_for_the_next_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _insert_renting(tmp_path, minutes_old=11, ready=True)
    provider = _Provider([_instance(age_minutes=11)])

    def failing(provider: VastProvider, contract_id: str) -> bool:
        raise RentError("still there")

    monkeypatch.setattr(recovery_module, "destroy_verified", failing)
    first = _run(provider)  # type: ignore[arg-type]

    assert first == "destroyed=0 lost=0 failed=1"
    row = _row(tmp_path)
    assert row.status == generator_servers.DESTROYING
    assert row.last_error == "still there"
    assert set(provider.instances) == {"c1"}

    monkeypatch.setattr(recovery_module, "destroy_verified", destroy_verified)
    second = _run(provider)  # type: ignore[arg-type]

    assert second == "destroyed=1 lost=0 failed=0"
    assert provider.destroyed == ["c1"]
    assert _row(tmp_path).status == generator_servers.DESTROYED


def test_second_tick_does_not_destroy_again(tmp_path: Path) -> None:
    _insert_renting(tmp_path, minutes_old=11, ready=True)
    provider = _Provider([_instance(age_minutes=11)])

    assert _run(provider) == "destroyed=1 lost=0 failed=0"  # type: ignore[arg-type]
    assert _run(provider) == "destroyed=0 lost=0 failed=0"  # type: ignore[arg-type]

    assert provider.destroyed == ["c1"]
    assert _row(tmp_path).status == generator_servers.DESTROYED


def test_vast_error_returns_an_outcome_without_raising(tmp_path: Path) -> None:
    _insert_renting(tmp_path, minutes_old=11, ready=True)

    class _Broken(_Provider):
        def list_instances(self) -> list[dict[str, Any]]:
            raise RentError("account list is down")

    outcome = _run(_Broken())  # type: ignore[arg-type]

    assert outcome == "vast_unavailable"
    assert _row(tmp_path).status == generator_servers.READY


def test_database_error_returns_an_outcome_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(_path: Path):
        raise DatabaseError("database is down")

    monkeypatch.setattr(flow_module, "connect", broken)
    provider = _Provider([_instance(age_minutes=11)])

    outcome = _run(provider)  # type: ignore[arg-type]

    assert outcome == "db_unavailable"
    assert provider.destroyed == []


def _insert_run(
    tmp_path: Path,
    *,
    run_id: str = _RUN_ID,
    profile: str = "dating/profile_1",
    status: str = "running",
    file: str | None = None,
) -> None:
    """Insert one run row and, when named, one finished item of it."""
    connection = _db(tmp_path)
    try:
        generator_runs.insert_run(
            connection,
            run_id=run_id,
            profiles=[profile],
            count=1,
            content_type="video",
            now=_iso(_NOW),
        )
        if file is not None:
            generator_runs.insert_plan(
                connection,
                run_id,
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
                _iso(_NOW),
            )
            generator_runs.mark_published(
                connection,
                run_id=run_id,
                profile=profile,
                file=file,
                now=_iso(_NOW),
            )
        connection.commit()
    finally:
        connection.close()


def _run_status(tmp_path: Path, run_id: str = _RUN_ID) -> str:
    connection = _db(tmp_path)
    try:
        row = generator_runs.get_run(connection, run_id)
    finally:
        connection.close()
    assert row is not None
    return row.status


def test_bound_server_of_an_unfinished_run_is_not_reaped(tmp_path: Path) -> None:
    _insert_renting(tmp_path, minutes_old=11, ready=True)
    _insert_run(tmp_path)
    connection = _db(tmp_path)
    try:
        outcome = recovery_module.recover(
            connection,
            _Provider([_instance(age_minutes=11)]),  # type: ignore[arg-type]
            now=_NOW,
        )
    finally:
        connection.close()

    assert (outcome.destroyed, outcome.lost, outcome.failed) == (0, 0, 0)
    assert _row(tmp_path).status == generator_servers.READY


def test_recovery_resumes_the_run_before_reaping_its_server(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles", name="dating/profile_1")
    _insert_run(tmp_path, status="done", file="dating_profile_1_20260101T000000Z_001.mp4")
    _insert_renting(tmp_path, minutes_old=11, ready=True)
    provider = _Provider([_instance(age_minutes=11)])

    outcome = _run(provider)  # type: ignore[arg-type]

    assert outcome == "destroyed=0 lost=0 failed=0"
    assert provider.destroyed == ["c1"]
    assert _row(tmp_path).status == generator_servers.DESTROYED
    assert _run_status(tmp_path) == "done"


def test_busy_generator_lock_does_not_resume_the_run(tmp_path: Path) -> None:
    _insert_run(tmp_path)
    lock = try_acquire(lock_path(tmp_path / "db", GENERATOR_LOCK))
    assert lock is not None
    provider = _Provider([_instance()])
    try:
        outcome = _run(provider)  # type: ignore[arg-type]
    finally:
        lock.release()

    assert outcome == "busy"
    assert provider.queries == 0
    assert _run_status(tmp_path) == "running"
