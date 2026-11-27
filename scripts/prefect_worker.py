#!/usr/bin/env python3
"""Close orphaned Prefect flow runs, then start the Content Factory worker.

The worker runs every flow in a subprocess. A container restart kills those
subprocesses, while Prefect keeps their flow runs ``RUNNING`` forever. This
wrapper runs before ``prefect worker start`` and moves runs of a worker that
stopped heartbeating to ``Crashed``.

A worker is considered dead when its last heartbeat is older than
:data:`HEARTBEAT_WINDOW` (five minutes), not when the server's ``status``
column says ``OFFLINE``: Prefect may report a live worker as offline for up to
three heartbeat intervals. Runs without the worker label, runs of live workers
and runs of other pools are never touched.

The cleanup is best-effort: any API error is logged and the worker still
starts. The wrapper must stay PID 1 after startup, so the final step is
``execvp``.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from loguru import logger

type Reconcile = Callable[[str], int]
type ExecWorker = Callable[[str, list[str]], Any]

WORK_POOL_LABEL = "prefect.work-pool.name"
WORKER_NAME_LABEL = "prefect.worker.name"
RUNNING_STATE = "RUNNING"
HEARTBEAT_WINDOW = timedelta(minutes=5)
PAGE_SIZE = 200
WORKER_LIMIT = 200
CRASH_MESSAGE = (
    "Flow run crashed by prefect_worker: its worker stopped heartbeating "
    "after the worker container restarted (issue #180)."
)
# Prefect 3 REST takes the target state under ``state``; a flat body answers
# 422 ``missing body.state`` and the run stays RUNNING (issue #180 regression).
CRASHED_STATE_BODY: dict[str, Any] = {
    "state": {
        "type": "CRASHED",
        "name": "Crashed",
        "message": CRASH_MESSAGE,
    },
}
RUNNING_RUNS_FILTER: dict[str, Any] = {
    "flow_runs": {"state": {"type": {"any_": [RUNNING_STATE]}}},
}


class PrefectApi(Protocol):
    """The Prefect API surface the cleanup needs."""

    async def read_workers(self, pool_name: str, body: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Return the workers of one pool matching ``body``."""

    async def read_flow_runs(self, body: Mapping[str, Any]) -> list[dict[str, Any]]:
        """Return the flow runs matching ``body``."""

    async def set_flow_run_state(self, flow_run_id: str, body: Mapping[str, Any]) -> Any:
        """Set the state of one flow run."""


class HttpPrefectApi:
    """Minimal Prefect REST client (httpx is already a project dependency)."""

    def __init__(
        self,
        base_url: str,
        api_key: str = "",
        *,
        timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    @classmethod
    def from_env(cls) -> HttpPrefectApi:
        """Build the client from ``PREFECT_API_URL`` and ``PREFECT_API_KEY``."""

        base_url = os.environ.get("PREFECT_API_URL", "").strip()
        if not base_url:
            raise RuntimeError("PREFECT_API_URL is not set")
        return cls(base_url, os.environ.get("PREFECT_API_KEY", "").strip())

    async def __aenter__(self) -> HttpPrefectApi:
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=self._timeout,
            transport=self._transport,
        )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("Prefect HTTP client is not open")
        return self._client

    async def _post(self, path: str, body: Mapping[str, Any]) -> httpx.Response:
        response = await self._http().post(path, json=dict(body))
        response.raise_for_status()
        return response

    async def read_workers(self, pool_name: str, body: Mapping[str, Any]) -> list[dict[str, Any]]:
        response = await self._post(
            f"/work_pools/{quote(pool_name, safe='')}/workers/filter",
            body,
        )
        return _json_array(response)

    async def read_flow_runs(self, body: Mapping[str, Any]) -> list[dict[str, Any]]:
        return _json_array(await self._post("/flow_runs/filter", body))

    async def set_flow_run_state(self, flow_run_id: str, body: Mapping[str, Any]) -> dict[str, Any]:
        response = await self._post(
            f"/flow_runs/{quote(flow_run_id, safe='')}/set_state",
            body,
        )
        if response.status_code == 204 or not response.content:
            return {}
        return _json_object(response)


def _json_array(response: httpx.Response) -> list[dict[str, Any]]:
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("Prefect API returned a non-array response")
    return [item for item in payload if isinstance(item, dict)]


def _json_object(response: httpx.Response) -> dict[str, Any]:
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Prefect API returned a non-object response")
    return payload


def _labels(run: Mapping[str, Any]) -> Mapping[str, Any]:
    labels = run.get("labels")
    return labels if isinstance(labels, Mapping) else {}


def _state_type(run: Mapping[str, Any]) -> str:
    state = run.get("state")
    if isinstance(state, Mapping):
        value = state.get("type")
        if isinstance(value, str):
            return value.upper()
    return ""


def is_pool_worker_run(run: Mapping[str, Any], pool_name: str) -> bool:
    """Return whether a run belongs to a labelled worker of ``pool_name``."""

    labels = _labels(run)
    if labels.get(WORK_POOL_LABEL) != pool_name:
        return False
    worker = labels.get(WORKER_NAME_LABEL)
    return isinstance(worker, str) and bool(worker)


def is_orphaned_run(
    run: Mapping[str, Any],
    live_workers: Sequence[str] | set[str],
    pool_name: str,
) -> bool:
    """Return whether a RUNNING run belongs to a worker without a heartbeat."""

    if _state_type(run) != RUNNING_STATE:
        return False
    if not is_pool_worker_run(run, pool_name):
        return False
    return _labels(run)[WORKER_NAME_LABEL] not in live_workers


def _heartbeat_cutoff(now: datetime | None) -> str:
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (moment - HEARTBEAT_WINDOW).isoformat()


def live_worker_filter(now: datetime | None = None) -> dict[str, Any]:
    """Return the worker filter for workers with a fresh heartbeat."""

    return {
        "workers": {"last_heartbeat_time": {"after_": _heartbeat_cutoff(now)}},
        "limit": WORKER_LIMIT,
    }


async def live_worker_names(
    prefect: PrefectApi,
    pool_name: str,
    *,
    now: datetime | None = None,
) -> set[str]:
    """Return the workers of ``pool_name`` with a heartbeat within the window."""

    workers = await prefect.read_workers(pool_name, live_worker_filter(now))
    names: set[str] = set()
    for worker in workers:
        name = worker.get("name")
        if isinstance(name, str) and name:
            names.add(name)
    return names


async def _read_running_runs(prefect: PrefectApi) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    offset = 0
    while True:
        body = {
            **RUNNING_RUNS_FILTER,
            "limit": PAGE_SIZE,
            "offset": offset,
        }
        page = await prefect.read_flow_runs(body)
        runs.extend(page)
        if len(page) < PAGE_SIZE:
            return runs
        offset += len(page)


async def reconcile_orphaned_runs(
    prefect: PrefectApi,
    pool_name: str,
    *,
    now: datetime | None = None,
) -> int:
    """Crash orphaned RUNNING runs of ``pool_name`` and return their count."""

    runs = await _read_running_runs(prefect)
    candidates = [run for run in runs if is_pool_worker_run(run, pool_name)]
    if not candidates:
        return 0
    live = await live_worker_names(prefect, pool_name, now=now)
    crashed = 0
    for run in candidates:
        if not is_orphaned_run(run, live, pool_name):
            continue
        run_id = run.get("id")
        if not isinstance(run_id, str) or not run_id:
            continue
        try:
            result = await prefect.set_flow_run_state(run_id, dict(CRASHED_STATE_BODY))
        except Exception as exc:  # one rejected run must not stop the pass
            logger.warning("could not crash flow run {} ({})", run_id, type(exc).__name__)
            continue
        status = result.get("status") if isinstance(result, Mapping) else None
        if result == {} or status == "ACCEPT":
            crashed += 1
            continue
        logger.warning("flow run {} state was not accepted (status={})", run_id, status)
    logger.info("closed {} orphaned flow run(s) in pool {}", crashed, pool_name)
    return crashed


async def _reconcile_from_env(pool_name: str, *, now: datetime | None = None) -> int:
    async with HttpPrefectApi.from_env() as prefect:
        return await reconcile_orphaned_runs(prefect, pool_name, now=now)


def reconcile_on_start(pool_name: str, *, now: datetime | None = None) -> int:
    """Run the cleanup synchronously (the worker entrypoint is synchronous)."""

    return asyncio.run(_reconcile_from_env(pool_name, now=now))


def worker_pool(argv: Sequence[str]) -> str | None:
    """Return the pool passed to ``prefect worker start``, if any."""

    for index, arg in enumerate(argv):
        if arg == "--pool":
            if index + 1 < len(argv):
                return argv[index + 1]
            return None
        if arg.startswith("--pool="):
            return arg.partition("=")[2]
    return None


def run(
    argv: Sequence[str],
    *,
    reconcile: Reconcile = reconcile_on_start,
    exec_worker: ExecWorker = os.execvp,
) -> int:
    """Reconcile orphaned runs, then start the worker with the same arguments."""

    pool = worker_pool(argv)
    if pool:
        try:
            crashed = reconcile(pool)
        except Exception as exc:
            logger.warning("worker start: orphan cleanup skipped ({})", type(exc).__name__)
        else:
            logger.info("worker start: orphan cleanup finished (crashed={})", crashed)
    else:
        logger.warning("worker start: --pool is missing, orphan cleanup skipped")
    exec_worker("prefect", ["prefect", "worker", "start", *argv])
    return 0


def main() -> int:
    return run(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
