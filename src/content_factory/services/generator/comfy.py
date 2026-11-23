"""ComfyUI HTTP client and the two-job generation queue."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
from loguru import logger

from content_factory.exceptions.generator import ComfyError, GeneratorError
from content_factory.schemas.generator.server import ServerConnection
from content_factory.services.generator import media
from content_factory.services.generator.plan import _PlannedItem

_MAX_IN_FLIGHT = 2
_MAX_ATTEMPTS = 3
_HISTORY_POLL = 10.0
_JOB_TIMEOUT = 1800.0
_REQUEST_TIMEOUT = 120.0
_HTTP_ERROR_LIMIT = 500


@dataclass(frozen=True, slots=True)
class _OutputFile:
    """One output file referenced by the ComfyUI history."""

    filename: str
    subfolder: str = ""
    folder_type: str = "output"


@dataclass(frozen=True, slots=True)
class _JobResult:
    """Outcome of one video: a saved source path or a failure reason."""

    item: _PlannedItem
    path: Path | None = None
    error: str | None = None


class _ComfyTransport(Protocol):
    """HTTP operations of ComfyUI used by the generation queue."""

    def submit(self, workflow: dict[str, Any]) -> str:
        """Queue a workflow and return its prompt id."""
        ...

    def history(self, prompt_id: str) -> dict[str, Any] | None:
        """Return the finished history item or ``None`` while running."""
        ...

    def download(self, output: _OutputFile) -> bytes:
        """Download one produced file."""
        ...

    def close(self) -> None:
        """Release the HTTP resources of the client."""
        ...


class ComfyClient:
    """Thin REST client of one ComfyUI server."""

    def __init__(
        self,
        connection: ServerConnection,
        *,
        client: httpx.Client | None = None,
        timeout: float = _REQUEST_TIMEOUT,
    ) -> None:
        """Store the server address; the HTTP client is lazy."""
        self._connection = connection
        self._client = client
        self._owned = client is None
        self._timeout = timeout

    def submit(self, workflow: dict[str, Any]) -> str:
        """Queue a workflow; HTTP 400 carries the rejected graph body.

        Raises:
            ComfyError: ComfyUI rejected the graph or returned no prompt id.
        """
        response = self._http().post(f"{self._connection.url()}/prompt", json={"prompt": workflow})
        if response.status_code == 400:
            raise ComfyError(f"ComfyUI отверг workflow: {response.text[:_HTTP_ERROR_LIMIT]}")
        _raise_status(response, "постановка workflow")
        prompt_id = _json_body(response, "постановка workflow").get("prompt_id")
        if not prompt_id:
            raise ComfyError(f"ComfyUI не вернул prompt_id: {response.text[:_HTTP_ERROR_LIMIT]}")
        return str(prompt_id)

    def history(self, prompt_id: str) -> dict[str, Any] | None:
        """Return the finished history item, or ``None`` while generating."""
        response = self._http().get(f"{self._connection.url()}/history/{prompt_id}")
        _raise_status(response, "история ComfyUI")
        item = _json_body(response, "история ComfyUI").get(prompt_id)
        return item if isinstance(item, dict) else None

    def download(self, output: _OutputFile) -> bytes:
        """Download one produced file from the server."""
        response = self._http().get(
            f"{self._connection.url()}/view",
            params={
                "filename": output.filename,
                "subfolder": output.subfolder,
                "type": output.folder_type,
            },
        )
        _raise_status(response, "скачивание результата")
        return response.content

    def close(self) -> None:
        """Close the HTTP client when this instance owns it."""
        if self._owned and self._client is not None and not self._client.is_closed:
            self._client.close()
        if self._owned:
            self._client = None

    def _http(self) -> httpx.Client:
        """Return the lazy HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(timeout=httpx.Timeout(self._timeout, connect=60.0))
        return self._client


def extract_output(history: dict[str, Any]) -> _OutputFile:
    """Return the first produced file of the history item.

    Args:
        history: Finished history item of one prompt.

    Returns:
        File reference accepted by :meth:`_ComfyTransport.download`.

    Raises:
        ComfyError: The history has no ``gifs``/``videos``/``images`` output.
    """
    outputs = history.get("outputs")
    if isinstance(outputs, dict):
        for output in outputs.values():
            if not isinstance(output, dict):
                continue
            for key in ("gifs", "videos", "images"):
                media_items = output.get(key)
                if isinstance(media_items, list) and media_items:
                    first = media_items[0]
                    if isinstance(first, dict):
                        return _OutputFile(
                            filename=str(first.get("filename") or ""),
                            subfolder=str(first.get("subfolder") or ""),
                            folder_type=str(first.get("type") or "output"),
                        )
    raise ComfyError(f"в истории ComfyUI нет файлов: {sorted(outputs) if outputs else []}")


def run_jobs(
    client: _ComfyTransport,
    items: Sequence[_PlannedItem],
    build: Callable[[_PlannedItem, int], dict[str, Any]],
    directory: Path,
    *,
    store: Callable[[Path, bytes], None] | None = None,
    attempts: int = _MAX_ATTEMPTS,
    poll_interval: float = _HISTORY_POLL,
    job_timeout: float = _JOB_TIMEOUT,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[_JobResult, ...]:
    """Generate every planned video on one server, two jobs in flight.

    At most two prompts are queued in ComfyUI: one runs and one waits. When
    a result is ready, the next prompt is submitted before the finished file
    is downloaded. A failed job is retried on the same server up to
    *attempts* times.

    Args:
        client: ComfyUI client.
        items: Planned videos in production order.
        build: Workflow builder called with the item and the attempt number.
        directory: Directory that receives downloaded silent sources.
        store: Saver of the downloaded bytes; defaults to the ffmpeg cut.
        attempts: Attempts per video before it fails.
        poll_interval: Seconds between history polls.
        job_timeout: Seconds a prompt may stay without a result.
        sleep: Pause function; tests replace it to stay fast.
        clock: Monotonic clock; tests replace it to simulate timeouts.

    Returns:
        One result per item, ordered by ``seq``.
    """
    directory.mkdir(parents=True, exist_ok=True)
    saver = store or _store_video
    queue: deque[tuple[_PlannedItem, int]] = deque((item, 1) for item in items)
    running: dict[str, _Running] = {}
    finished: dict[int, _JobResult] = {}
    while queue or running:
        _fill(client, build, queue, running, finished, attempts, clock)
        if not running:
            continue
        ready = _ready(client, running)
        if ready is None:
            _expire(client, build, queue, running, finished, attempts, job_timeout, clock)
            if running:
                sleep(poll_interval)
            continue
        prompt_id, history = ready
        job = running.pop(prompt_id)
        _fill(client, build, queue, running, finished, attempts, clock)
        _download(client, history, job, directory, saver, queue, finished, attempts)
    return tuple(finished[index] for index in sorted(finished))


@dataclass(frozen=True, slots=True)
class _Running:
    """Prompt queued on the server and awaiting its history."""

    item: _PlannedItem
    attempt: int
    started: float


def _fill(
    client: _ComfyTransport,
    build: Callable[[_PlannedItem, int], dict[str, Any]],
    queue: deque[tuple[_PlannedItem, int]],
    running: dict[str, _Running],
    finished: dict[int, _JobResult],
    attempts: int,
    clock: Callable[[], float],
) -> None:
    """Submit prompts until two are in flight or the queue is empty."""
    while queue and len(running) < _MAX_IN_FLIGHT:
        item, attempt = queue.popleft()
        try:
            prompt_id = client.submit(build(item, attempt))
        except GeneratorError as ex:
            _retry(item, attempt, ex, queue, finished, attempts, "постановка")
            continue
        running[prompt_id] = _Running(item=item, attempt=attempt, started=clock())


def _ready(
    client: _ComfyTransport,
    running: dict[str, _Running],
) -> tuple[str, dict[str, Any]] | None:
    """Return the first prompt whose history is ready."""
    for prompt_id in list(running):
        history = client.history(prompt_id)
        if history is not None:
            return prompt_id, history
    return None


def _download(
    client: _ComfyTransport,
    history: dict[str, Any],
    job: _Running,
    directory: Path,
    store: Callable[[Path, bytes], None],
    queue: deque[tuple[_PlannedItem, int]],
    finished: dict[int, _JobResult],
    attempts: int,
) -> None:
    """Download a ready result and save it, or retry the job."""
    try:
        output = extract_output(history)
        content = client.download(output)
        if not content:
            raise ComfyError("ComfyUI вернул пустой файл")
        path = directory / job.item.filename
        store(path, content)
    except (GeneratorError, OSError) as ex:
        _retry(job.item, job.attempt, ex, queue, finished, attempts, "скачивание")
        return
    finished[job.item.seq] = _JobResult(item=job.item, path=path)
    logger.bind(file=path.name, seed=job.item.seed).info("Исходник видео сохранён")


def _expire(
    client: _ComfyTransport,
    build: Callable[[_PlannedItem, int], dict[str, Any]],
    queue: deque[tuple[_PlannedItem, int]],
    running: dict[str, _Running],
    finished: dict[int, _JobResult],
    attempts: int,
    job_timeout: float,
    clock: Callable[[], float],
) -> None:
    """Move prompts without a result for *job_timeout* back to the queue."""
    now = clock()
    for prompt_id, job in list(running.items()):
        if now - job.started <= job_timeout:
            continue
        del running[prompt_id]
        error = ComfyError(f"нет результата за {job_timeout:g} с")
        _retry(job.item, job.attempt, error, queue, finished, attempts, "ожидание")
        _fill(client, build, queue, running, finished, attempts, clock)


def _retry(
    item: _PlannedItem,
    attempt: int,
    error: Exception,
    queue: deque[tuple[_PlannedItem, int]],
    finished: dict[int, _JobResult],
    attempts: int,
    stage: str,
) -> None:
    """Requeue a failed job or record its final failure."""
    if attempt < attempts:
        logger.bind(
            file=item.filename,
            attempt=attempt,
            error=str(error),
            error_type=type(error).__name__,
        ).warning(f"Сбой на этапе «{stage}» — повтор")
        queue.append((item, attempt + 1))
        return
    logger.bind(
        file=item.filename,
        attempts=attempt,
        error=str(error),
        error_type=type(error).__name__,
    ).error(f"Сбой на этапе «{stage}» — ролик пропущен")
    finished[item.seq] = _JobResult(item=item, error=str(error))


def _store_video(path: Path, content: bytes) -> None:
    """Write the downloaded mp4 and strip the audio of the model."""
    raw = path.with_name(f"{path.name}.raw")
    raw.write_bytes(content)
    try:
        media.strip_audio(raw, path)
    finally:
        raw.unlink(missing_ok=True)


def _raise_status(response: httpx.Response, stage: str) -> None:
    """Convert an unsuccessful HTTP response into :class:`ComfyError`."""
    if response.is_success:
        return
    raise ComfyError(
        f"ComfyUI: ошибка HTTP {response.status_code} на этапе «{stage}»: "
        f"{response.text[:_HTTP_ERROR_LIMIT]}"
    )


def _json_body(response: httpx.Response, stage: str) -> dict[str, Any]:
    """Parse a ComfyUI JSON object or raise :class:`ComfyError`."""
    try:
        payload: Any = response.json()
    except ValueError as ex:
        raise ComfyError(f"ComfyUI вернул не JSON на этапе «{stage}»") from ex
    if not isinstance(payload, dict):
        raise ComfyError(f"ComfyUI вернул не объект на этапе «{stage}»")
    return payload
