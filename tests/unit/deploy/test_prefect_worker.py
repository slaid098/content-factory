"""Orphaned flow-run cleanup before the Content Factory worker starts."""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts" / "prefect_worker.py"
_NOW = datetime(2026, 11, 27, 3, 0, tzinfo=UTC)


def _load_worker() -> ModuleType:
    """Load ``scripts/prefect_worker.py`` without making scripts a package."""
    spec = importlib.util.spec_from_file_location("prefect_worker", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def worker() -> ModuleType:
    return _load_worker()


class _FakeApi:
    def __init__(
        self,
        runs: list[dict[str, Any]],
        workers: list[dict[str, Any]] | None = None,
        *,
        state_result: Any = None,
    ) -> None:
        self._runs = runs
        self._workers = workers or []
        self._state_result = {"status": "ACCEPT"} if state_result is None else state_result
        self.worker_bodies: list[dict[str, Any]] = []
        self.run_bodies: list[dict[str, Any]] = []
        self.state_calls: list[tuple[str, dict[str, Any]]] = []

    async def read_workers(self, pool_name: str, body: dict[str, Any]) -> list[dict[str, Any]]:
        assert pool_name == "content-factory"
        self.worker_bodies.append(dict(body))
        return list(self._workers)

    async def read_flow_runs(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        self.run_bodies.append(dict(body))
        return list(self._runs)

    async def set_flow_run_state(self, flow_run_id: str, body: dict[str, Any]) -> Any:
        self.state_calls.append((flow_run_id, dict(body)))
        return self._state_result


class _PartlyFailingApi(_FakeApi):
    def __init__(self, runs: list[dict[str, Any]]) -> None:
        super().__init__(runs)
        self._call = 0

    async def set_flow_run_state(self, flow_run_id: str, body: dict[str, Any]) -> Any:
        self.state_calls.append((flow_run_id, dict(body)))
        self._call += 1
        if self._call == 1:
            raise RuntimeError("Prefect API is down")
        return {"status": "ACCEPT"}


def _run(
    run_id: str = "run-1",
    *,
    state: str = "RUNNING",
    pool: str | None = "content-factory",
    worker: str | None = "worker-a",
) -> dict[str, Any]:
    labels: dict[str, Any] = {}
    if pool is not None:
        labels["prefect.work-pool.name"] = pool
    if worker is not None:
        labels["prefect.worker.name"] = worker
    return {"id": run_id, "state": {"type": state}, "labels": labels}


def test_orphaned_run_is_crashed(worker: ModuleType) -> None:
    api = _FakeApi([_run()])

    crashed = asyncio.run(worker.reconcile_orphaned_runs(api, "content-factory", now=_NOW))

    assert crashed == 1
    run_id, body = api.state_calls[0]
    assert run_id == "run-1"
    assert body["type"] == "CRASHED"
    assert body["name"] == "Crashed"
    assert "heartbeat" in body["message"]


def test_live_worker_with_a_fresh_heartbeat_is_untouched(worker: ModuleType) -> None:
    api = _FakeApi([_run()], [{"name": "worker-a", "status": "OFFLINE"}])

    crashed = asyncio.run(worker.reconcile_orphaned_runs(api, "content-factory", now=_NOW))

    assert crashed == 0
    assert api.state_calls == []
    body = api.worker_bodies[0]
    assert "status" not in body["workers"]
    assert body["workers"]["last_heartbeat_time"]["after_"] == "2026-11-27T02:55:00+00:00"


def test_run_without_a_worker_label_is_untouched(worker: ModuleType) -> None:
    api = _FakeApi([_run(worker=None)])

    crashed = asyncio.run(worker.reconcile_orphaned_runs(api, "content-factory", now=_NOW))

    assert crashed == 0
    assert api.worker_bodies == []
    assert api.state_calls == []


def test_run_of_another_pool_is_untouched(worker: ModuleType) -> None:
    api = _FakeApi([_run(pool="youtube-kit-worker")])

    crashed = asyncio.run(worker.reconcile_orphaned_runs(api, "content-factory", now=_NOW))

    assert crashed == 0
    assert api.state_calls == []


def test_terminal_run_is_untouched(worker: ModuleType) -> None:
    api = _FakeApi([_run(state="COMPLETED")])

    crashed = asyncio.run(worker.reconcile_orphaned_runs(api, "content-factory", now=_NOW))

    assert crashed == 0
    assert api.state_calls == []


def test_abort_is_not_counted(worker: ModuleType) -> None:
    api = _FakeApi([_run()], state_result={"status": "ABORT", "state": None})

    crashed = asyncio.run(worker.reconcile_orphaned_runs(api, "content-factory", now=_NOW))

    assert crashed == 0
    assert len(api.state_calls) == 1


def test_empty_204_body_counts_as_crashed(worker: ModuleType) -> None:
    api = _FakeApi([_run()], state_result={})

    crashed = asyncio.run(worker.reconcile_orphaned_runs(api, "content-factory", now=_NOW))

    assert crashed == 1


def test_one_rejected_run_does_not_stop_the_pass(worker: ModuleType) -> None:
    api = _PartlyFailingApi([_run("run-1"), _run("run-2")])

    crashed = asyncio.run(worker.reconcile_orphaned_runs(api, "content-factory", now=_NOW))

    assert crashed == 1
    assert [calls[0] for calls in api.state_calls] == ["run-1", "run-2"]


def test_running_runs_are_paged(worker: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    class _PagedApi(_FakeApi):
        async def read_flow_runs(self, body: dict[str, Any]) -> list[dict[str, Any]]:
            self.run_bodies.append(dict(body))
            offset = body["offset"]
            return list(self._runs[offset : offset + body["limit"]])

    monkeypatch.setattr(worker, "PAGE_SIZE", 2)
    api = _PagedApi([_run("run-1"), _run("run-2"), _run("run-3")])

    crashed = asyncio.run(worker.reconcile_orphaned_runs(api, "content-factory", now=_NOW))

    assert crashed == 3
    assert [body["offset"] for body in api.run_bodies] == [0, 2]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--pool", "content-factory", "--limit", "2"], "content-factory"),
        (["--limit", "2", "--pool=content-factory"], "content-factory"),
        (["--limit", "2"], None),
        (["--pool"], None),
    ],
)
def test_worker_pool_reads_both_option_forms(
    worker: ModuleType, argv: list[str], expected: str | None
) -> None:
    assert worker.worker_pool(argv) == expected


def test_run_passes_the_worker_arguments_unchanged(worker: ModuleType) -> None:
    calls: list[tuple[str, Any]] = []

    def reconcile(pool: str) -> int:
        calls.append(("reconcile", pool))
        return 0

    def exec_worker(file: str, args: list[str]) -> None:
        calls.append((file, args))

    code = worker.run(
        ["--pool", "content-factory", "--limit", "2"],
        reconcile=reconcile,
        exec_worker=exec_worker,
    )

    assert code == 0
    assert calls == [
        ("reconcile", "content-factory"),
        ("prefect", ["prefect", "worker", "start", "--pool", "content-factory", "--limit", "2"]),
    ]


def test_reconcile_error_still_starts_the_worker(worker: ModuleType) -> None:
    calls: list[tuple[str, list[str]]] = []

    def reconcile(pool: str) -> int:
        raise RuntimeError("Prefect API is down")

    def exec_worker(file: str, args: list[str]) -> None:
        calls.append((file, args))

    code = worker.run(
        ["--pool", "content-factory", "--limit", "2"],
        reconcile=reconcile,
        exec_worker=exec_worker,
    )

    assert code == 0
    assert calls == [
        ("prefect", ["prefect", "worker", "start", "--pool", "content-factory", "--limit", "2"])
    ]


def test_missing_pool_skips_cleanup_but_starts_the_worker(worker: ModuleType) -> None:
    calls: list[tuple[str, list[str]]] = []

    def reconcile(pool: str) -> int:
        raise AssertionError("cleanup must not run without a pool")

    def exec_worker(file: str, args: list[str]) -> None:
        calls.append((file, args))

    code = worker.run(
        ["--limit", "2"],
        reconcile=reconcile,
        exec_worker=exec_worker,
    )

    assert code == 0
    assert calls == [("prefect", ["prefect", "worker", "start", "--limit", "2"])]


def test_http_client_returns_the_orchestration_status(worker: ModuleType) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/flow_runs/run-1/set_state"
        return httpx.Response(200, json={"status": "ABORT", "state": None})

    async def scenario() -> dict[str, Any]:
        api = worker.HttpPrefectApi(
            "http://prefect.test/api",
            transport=httpx.MockTransport(handler),
        )
        async with api:
            return await api.set_flow_run_state("run-1", {"type": "CRASHED"})

    assert asyncio.run(scenario()) == {"status": "ABORT", "state": None}


def test_http_client_treats_204_as_an_empty_result(worker: ModuleType) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(204)

    async def scenario() -> dict[str, Any]:
        api = worker.HttpPrefectApi(
            "http://prefect.test/api",
            transport=httpx.MockTransport(handler),
        )
        async with api:
            return await api.set_flow_run_state("run-1", {"type": "CRASHED"})

    assert asyncio.run(scenario()) == {}
