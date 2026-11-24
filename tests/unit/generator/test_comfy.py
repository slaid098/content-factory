"""ComfyUI queue: two jobs in flight and retries on empty results."""

from __future__ import annotations

import contextlib
import json
import random
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.unit.generator.helpers import fixture_profile

from content_factory.exceptions.generator import ComfyError, SafetyError
from content_factory.schemas.generator.server import ServerConnection
from content_factory.services.generator import comfy, media
from content_factory.services.generator.comfy import (
    ComfyClient,
    _OutputFile,
    extract_output,
    run_jobs,
)
from content_factory.services.generator.plan import _PlannedItem, plan_batch


class _Transport:
    """ComfyUI transport with controllable download results."""

    def __init__(self, empty_downloads: int = 0) -> None:
        self.submitted: list[str] = []
        self.downloaded: list[int] = []
        self.empty_left = empty_downloads
        self.history_calls = 0
        self.closed = False

    def submit(self, workflow: dict[str, Any]) -> str:
        del workflow
        prompt = f"p{len(self.submitted) + 1}"
        self.submitted.append(prompt)
        return prompt

    def history(self, prompt_id: str) -> dict[str, Any]:
        self.history_calls += 1
        return {"outputs": {"15": {"gifs": [{"filename": f"{prompt_id}.mp4"}]}}}

    def system_stats(self) -> dict[str, Any]:
        return {"system": {}}

    def download(self, output: _OutputFile) -> bytes:
        del output
        self.downloaded.append(len(self.submitted))
        if self.empty_left > 0:
            self.empty_left -= 1
            return b""
        return b"video"

    def close(self) -> None:
        self.closed = True


def _items(tmp_path: Path, count: int = 5) -> tuple[_PlannedItem, ...]:
    profile = fixture_profile(tmp_path / "profiles", looks=20)
    return plan_batch(profile, count, "run", rng=random.Random(1))


def _build(item: _PlannedItem, attempt: int) -> dict[str, Any]:
    return {"attempt": attempt, "file": item.filename}


def test_at_most_two_prompts_are_in_flight_and_next_goes_before_download(
    tmp_path: Path,
) -> None:
    transport = _Transport()
    items = _items(tmp_path)

    results = run_jobs(
        transport,
        items,
        _build,
        tmp_path / "source",
        store=lambda path, content: path.write_bytes(content),
    )

    assert [result.path is not None for result in results] == [True] * 5
    assert transport.downloaded[0] == 3
    assert max(transport.downloaded) <= len(items)
    assert len({result.item.seq for result in results}) == 5


def test_empty_download_is_retried_on_the_same_server(tmp_path: Path) -> None:
    transport = _Transport(empty_downloads=1)
    items = _items(tmp_path, count=1)
    attempts: list[int] = []

    def build(item: _PlannedItem, attempt: int) -> dict[str, Any]:
        attempts.append(attempt)
        return _build(item, attempt)

    results = run_jobs(
        transport,
        items,
        build,
        tmp_path / "source",
        store=lambda path, content: path.write_bytes(content),
    )

    assert attempts == [1, 2]
    assert results[0].path is not None
    assert results[0].path.is_file()


def test_item_fails_after_three_empty_downloads(tmp_path: Path) -> None:
    transport = _Transport(empty_downloads=10)
    items = _items(tmp_path, count=1)

    results = run_jobs(
        transport,
        items,
        _build,
        tmp_path / "source",
        store=lambda path, content: path.write_bytes(content),
        attempts=3,
    )

    assert len(transport.submitted) == 3
    assert results[0].path is None
    assert "пустой файл" in (results[0].error or "")


class _StuckTransport:
    """ComfyUI transport whose prompts never appear in history."""

    def __init__(self) -> None:
        self.submitted = 0

    def submit(self, workflow: dict[str, Any]) -> str:
        del workflow
        self.submitted += 1
        return f"p{self.submitted}"

    def history(self, prompt_id: str) -> None:
        del prompt_id
        return None

    def system_stats(self) -> dict[str, Any]:
        return {"system": {}}

    def download(self, output: _OutputFile) -> bytes:
        del output
        raise AssertionError("no ready prompt should be downloaded")

    def close(self) -> None:
        return None


def test_stuck_prompt_raises_after_job_timeout_without_a_retry(tmp_path: Path) -> None:
    transport = _StuckTransport()
    items = _items(tmp_path, count=1)
    now = 0.0

    def clock() -> float:
        return now

    def sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    with pytest.raises(ComfyError, match="нет результата за 5 с"):
        run_jobs(
            transport,
            items,
            _build,
            tmp_path / "source",
            store=lambda path, content: path.write_bytes(content),
            attempts=3,
            poll_interval=10,
            job_timeout=5,
            sleep=sleep,
            clock=clock,
        )

    assert transport.submitted == 1


class _HealthTransport(_Transport):
    """Transport with controllable health failures and delayed history."""

    def __init__(self, health_failures: int = 0, polls: int = 5) -> None:
        super().__init__()
        self.health_left = health_failures
        self.health_calls = 0
        self.polls_left = polls

    def history(self, prompt_id: str) -> dict[str, Any] | None:
        self.history_calls += 1
        if self.polls_left > 0:
            self.polls_left -= 1
            return None
        return {"outputs": {"15": {"gifs": [{"filename": f"{prompt_id}.mp4"}]}}}

    def system_stats(self) -> dict[str, Any]:
        self.health_calls += 1
        if self.health_left > 0:
            self.health_left -= 1
            raise ComfyError("connection reset")
        return {"system": {}}


def test_two_health_errors_do_not_stop_generation(tmp_path: Path) -> None:
    transport = _HealthTransport(health_failures=2)
    items = _items(tmp_path, count=1)
    now = 0.0

    def clock() -> float:
        return now

    def sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    results = run_jobs(
        transport,
        items,
        _build,
        tmp_path / "source",
        store=lambda path, content: path.write_bytes(content),
        poll_interval=10,
        job_timeout=600,
        health_interval=10,
        sleep=sleep,
        clock=clock,
    )

    assert transport.health_calls >= 3
    assert results[0].path is not None


def test_three_health_errors_stop_the_batch_and_its_retries(tmp_path: Path) -> None:
    transport = _HealthTransport(health_failures=10, polls=100)
    items = _items(tmp_path, count=1)
    now = 0.0

    def clock() -> float:
        return now

    def sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    with pytest.raises(ComfyError, match="сервер перестал отвечать"):
        run_jobs(
            transport,
            items,
            _build,
            tmp_path / "source",
            store=lambda path, content: path.write_bytes(content),
            poll_interval=10,
            job_timeout=600,
            health_interval=10,
            health_fail_limit=3,
            sleep=sleep,
            clock=clock,
        )

    assert transport.health_calls == 3
    assert len(transport.submitted) == 1


class _BrokenDownload(_Transport):
    """Transport whose first download fails like a dropped server."""

    def __init__(self) -> None:
        super().__init__()
        self.download_attempts = 0

    def download(self, output: _OutputFile) -> bytes:
        self.download_attempts += 1
        if self.download_attempts == 1:
            raise ComfyError("ComfyUI: ошибка HTTP 502 на этапе «скачивание результата»")
        return super().download(output)


def test_download_error_is_retried_on_the_same_server(tmp_path: Path) -> None:
    transport = _BrokenDownload()
    items = _items(tmp_path, count=1)

    results = run_jobs(
        transport,
        items,
        _build,
        tmp_path / "source",
        store=lambda path, content: path.write_bytes(content),
        attempts=3,
    )

    assert transport.download_attempts == 2
    assert len(transport.submitted) == 2
    assert results[0].path is not None


def test_missing_output_is_an_error() -> None:
    with pytest.raises(ComfyError, match="нет файлов"):
        extract_output({"outputs": {}})


def _connection() -> ServerConnection:
    return ServerConnection(
        contract_id="1",
        ip="127.0.0.1",
        port=8188,
        gpu="L40S",
        image="image",
    )


def _client(handler: Any) -> ComfyClient:
    return ComfyClient(_connection(), client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_client_submits_workflow_and_returns_prompt_id() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"prompt_id": "p1"}, request=request)

    with contextlib.closing(_client(handler)) as client:
        assert client.submit({"1": {}}) == "p1"

    assert seen["path"] == "/prompt"
    assert seen["body"] == {"prompt": {"1": {}}}


def test_client_reports_rejected_graph_and_http_errors() -> None:
    def reject(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="bad graph", request=request)

    with (
        contextlib.closing(_client(reject)) as client,
        pytest.raises(ComfyError, match="отверг workflow: bad graph"),
    ):
        client.submit({})

    def fail(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server down", request=request)

    with contextlib.closing(_client(fail)) as client, pytest.raises(ComfyError, match="HTTP 500"):
        client.submit({})


def test_client_requires_prompt_id_and_json_object() -> None:
    def no_prompt(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={}, request=request)

    with (
        contextlib.closing(_client(no_prompt)) as client,
        pytest.raises(ComfyError, match="prompt_id"),
    ):
        client.submit({})

    def not_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="plain", request=request)

    with (
        contextlib.closing(_client(not_json)) as client,
        pytest.raises(ComfyError, match="не JSON"),
    ):
        client.submit({})

    def not_object(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[], request=request)

    with (
        contextlib.closing(_client(not_object)) as client,
        pytest.raises(ComfyError, match="не объект"),
    ):
        client.submit({})


def test_client_reads_history_and_handles_empty_or_broken_answers() -> None:
    def ready(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"p1": {"outputs": {}}}, request=request)

    with contextlib.closing(_client(ready)) as client:
        assert client.history("p1") == {"outputs": {}}

    def empty(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={}, request=request)

    with contextlib.closing(_client(empty)) as client:
        assert client.history("p1") is None

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="gateway", request=request)

    with contextlib.closing(_client(broken)) as client, pytest.raises(ComfyError, match="HTTP 502"):
        client.history("p1")


def test_client_reads_system_stats_without_leaking_the_body() -> None:
    seen: dict[str, str] = {}

    def healthy(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        return httpx.Response(200, json={"system": {"os": "posix"}}, request=request)

    with contextlib.closing(_client(healthy)) as client:
        assert client.system_stats() == {"system": {"os": "posix"}}

    assert seen["path"] == "/system_stats"

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="secret body", request=request)

    with (
        contextlib.closing(_client(broken)) as client,
        pytest.raises(ComfyError, match="HTTP 500") as raised,
    ):
        client.system_stats()

    assert "secret body" not in str(raised.value)

    def not_json(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="plain", request=request)

    with (
        contextlib.closing(_client(not_json)) as client,
        pytest.raises(ComfyError, match="не JSON"),
    ):
        client.system_stats()


def test_client_downloads_with_filename_params() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, content=b"mp4", request=request)

    with contextlib.closing(_client(handler)) as client:
        content = client.download(_OutputFile(filename="a.mp4", subfolder="s", folder_type="temp"))

    assert content == b"mp4"
    assert seen == {"filename": "a.mp4", "subfolder": "s", "type": "temp"}


def test_client_download_error_is_reported() -> None:
    def missing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="gone", request=request)

    with (
        contextlib.closing(_client(missing)) as client,
        pytest.raises(ComfyError, match="HTTP 404"),
    ):
        client.download(_OutputFile(filename="a.mp4"))


def test_close_only_closes_owned_clients() -> None:
    injected = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    comfy_client = ComfyClient(_connection(), client=injected)

    comfy_client.close()

    assert not injected.is_closed
    injected.close()

    owned = ComfyClient(_connection())
    http = owned._http()
    owned.close()
    owned.close()

    assert http.is_closed
    assert owned._client is None


def test_extract_output_accepts_all_output_keys_and_skips_broken_items() -> None:
    history = {
        "outputs": {
            "1": "broken",
            "2": {"gifs": []},
            "3": {"videos": [{"filename": "v.mp4", "subfolder": "s", "type": "temp"}]},
            "4": {"images": [{"filename": "i.png"}]},
        }
    }

    output = extract_output(history)

    assert output == _OutputFile(filename="v.mp4", subfolder="s", folder_type="temp")


def test_extract_output_uses_image_fallback() -> None:
    output = extract_output({"outputs": {"4": {"images": [{"filename": "i.png"}]}}})

    assert output == _OutputFile(filename="i.png")


def test_submit_failures_are_retried_on_the_same_server(tmp_path: Path) -> None:
    transport = _SubmitFailures()
    items = _items(tmp_path, count=1)
    attempts: list[int] = []

    def build(item: _PlannedItem, attempt: int) -> dict[str, Any]:
        attempts.append(attempt)
        return _build(item, attempt)

    results = run_jobs(
        transport,
        items,
        build,
        tmp_path / "source",
        store=lambda path, content: path.write_bytes(content),
        attempts=3,
    )

    assert transport.attempts == 3
    assert attempts == [1, 2, 3]
    assert results[0].path is not None


class _SubmitFailures(_Transport):
    """Transport whose first two submits are rejected."""

    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def submit(self, workflow: dict[str, Any]) -> str:
        self.attempts += 1
        if self.attempts <= 2:
            raise ComfyError("rejected")
        return super().submit(workflow)


def test_store_failure_is_retried(tmp_path: Path) -> None:
    transport = _Transport()
    items = _items(tmp_path, count=1)
    calls: list[int] = []

    def store(path: Path, content: bytes) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk hiccup")
        path.write_bytes(content)

    results = run_jobs(transport, items, _build, tmp_path / "source", store=store, attempts=2)

    assert len(calls) == 2
    assert results[0].path is not None


def test_store_video_strips_model_audio_and_drops_the_raw_file(tmp_path: Path, monkeypatch) -> None:
    def strip(source: Path, target: Path) -> None:
        assert source.read_bytes() == b"raw"
        target.write_bytes(b"clean")

    monkeypatch.setattr(media, "strip_audio", strip)
    target = tmp_path / "video.mp4"

    comfy._store_video(target, b"raw")

    assert target.read_bytes() == b"clean"
    assert not (tmp_path / "video.mp4.raw").exists()


def test_verify_rejection_regenerates_the_item_with_the_next_attempt(tmp_path: Path) -> None:
    transport = _Transport()
    items = _items(tmp_path, count=1)
    seen: list[int] = []

    def verify(item: _PlannedItem, path: Path, attempt: int) -> str | None:
        assert item.seq == 1
        assert path.is_file()
        seen.append(attempt)
        return "спина" if attempt == 1 else None

    results = run_jobs(
        transport,
        items,
        _build,
        tmp_path / "source",
        store=lambda path, content: path.write_bytes(content),
        verify=verify,
        safety_frame_attempts=2,
    )

    assert seen == [1, 2]
    assert len(transport.submitted) == 2
    assert results[0].path is not None


def test_second_verify_rejection_fails_with_its_reason(tmp_path: Path) -> None:
    transport = _Transport()
    items = _items(tmp_path, count=1)
    calls: list[int] = []

    def verify(item: _PlannedItem, path: Path, attempt: int) -> str | None:
        del item, path
        calls.append(attempt)
        return "спина"

    results = run_jobs(
        transport,
        items,
        _build,
        tmp_path / "source",
        store=lambda path, content: path.write_bytes(content),
        verify=verify,
        safety_frame_attempts=2,
    )

    assert calls == [1, 2]
    assert len(transport.submitted) == 2
    assert results[0].path is None
    assert results[0].error == "спина"


def test_verify_error_fails_closed_without_regeneration(tmp_path: Path) -> None:
    transport = _Transport()
    items = _items(tmp_path, count=1)

    def verify(item: _PlannedItem, path: Path, attempt: int) -> str | None:
        del item, path, attempt
        raise SafetyError("Clef недоступен")

    results = run_jobs(
        transport,
        items,
        _build,
        tmp_path / "source",
        store=lambda path, content: path.write_bytes(content),
        verify=verify,
        safety_frame_attempts=2,
    )

    assert len(transport.submitted) == 1
    assert results[0].path is None
    assert "Clef недоступен" in (results[0].error or "")
    assert not (tmp_path / "source" / items[0].filename).exists()


def test_verify_rejection_at_the_last_allowed_attempt_fails_immediately(tmp_path: Path) -> None:
    transport = _Transport()
    items = _items(tmp_path, count=1)

    results = run_jobs(
        transport,
        items,
        _build,
        tmp_path / "source",
        store=lambda path, content: path.write_bytes(content),
        verify=lambda item, path, attempt: "спина",
        safety_frame_attempts=1,
    )

    assert len(transport.submitted) == 1
    assert results[0].error == "спина"


def test_comfy_failure_does_not_spend_the_safety_regeneration(tmp_path: Path) -> None:
    transport = _Transport(empty_downloads=1)
    items = _items(tmp_path, count=1)
    seen: list[int] = []

    def verify(item: _PlannedItem, path: Path, attempt: int) -> str | None:
        del item
        assert path.is_file()
        seen.append(attempt)
        return "спина" if len(seen) == 1 else None

    results = run_jobs(
        transport,
        items,
        _build,
        tmp_path / "source",
        store=lambda path, content: path.write_bytes(content),
        verify=verify,
        safety_frame_attempts=2,
        attempts=3,
    )

    assert seen == [2, 3]
    assert len(transport.submitted) == 3
    assert results[0].path is not None
