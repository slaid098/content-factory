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

    def system_stats(self) -> dict[str, Any]:
        """Return the server health payload of ``/system_stats``."""
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

    def system_stats(self) -> dict[str, Any]:
        """Return the health payload of the server without its raw body.

        Returns:
            JSON object of ``/system_stats``.

        Raises:
            ComfyError: The request failed, returned an HTTP error or a non-JSON body.
        """
        try:
            response = self._http().get(f"{self._connection.url()}/system_stats")
        except httpx.HTTPError as ex:
            raise ComfyError(
                f"ComfyUI не ответил на этапе «проверка здоровья»: {type(ex).__name__}"
            ) from ex
        if not response.is_success:
            raise ComfyError(
                f"ComfyUI: ошибка HTTP {response.status_code} на этапе «проверка здоровья»"
            )
        return _json_body(response, "проверка здоровья")

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
    verify: Callable[[_PlannedItem, Path, int], str | None] | None = None,
    safety_frame_attempts: int = 2,
    poll_interval: float = _HISTORY_POLL,
    job_timeout: float = _JOB_TIMEOUT,
    health_interval: float = 60.0,
    health_fail_limit: int = 3,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[_JobResult, ...]:
    """Generate every planned video on one server, two jobs in flight.

    At most two prompts are queued in ComfyUI: one runs and one waits. When
    a result is ready, the next prompt is submitted before the finished file
    is downloaded. A failed job is retried on the same server up to
    *attempts* times. When *verify* rejects a saved source, the item goes
    back into the queue with the next attempt and a new workflow seed; after
    *safety_frame_attempts* rejections the video fails with the reason.

    The server itself is never retried: ``/system_stats`` is checked every
    *health_interval* seconds, and *health_fail_limit* consecutive errors
    raise :class:`ComfyError`. A prompt without a result for *job_timeout*
    raises it too, so the caller can destroy the paid server immediately.

    Args:
        client: ComfyUI client.
        items: Planned videos in production order.
        build: Workflow builder called with the item and the attempt number.
        directory: Directory that receives downloaded silent sources.
        store: Saver of the downloaded bytes; defaults to the ffmpeg cut.
        attempts: ComfyUI attempts per video before it fails.
        verify: Safety check of a saved source; returns a defect reason.
        safety_frame_attempts: Safety attempts per video before it fails.
        poll_interval: Seconds between history polls.
        job_timeout: Seconds a prompt may stay without a result.
        health_interval: Seconds between ``/system_stats`` checks.
        health_fail_limit: Consecutive health errors that declare the server dead.
        sleep: Pause function; tests replace it to stay fast.
        clock: Monotonic clock; tests replace it to simulate timeouts.

    Returns:
        One result per item, ordered by ``seq``.

    Raises:
        ComfyError: The server stopped answering or a prompt timed out.
    """
    directory.mkdir(parents=True, exist_ok=True)
    saver = store or _store_video
    state = _BatchState(
        queue=deque((item, 1) for item in items),
        running={},
        finished={},
        comfy_failures={},
        safety_rejections={},
    )
    health = _HealthProbe(client, health_interval, health_fail_limit, clock, clock())
    while state.queue or state.running:
        health.check(state)
        _fill(client, build, state, attempts, clock)
        if not state.running:
            continue
        ready = _ready(client, state.running)
        if ready is None:
            _expire(state, job_timeout, clock)
            if state.running:
                sleep(poll_interval)
            continue
        prompt_id, history = ready
        job = state.running.pop(prompt_id)
        _fill(client, build, state, attempts, clock)
        _download(
            client,
            history,
            job,
            directory,
            saver,
            state,
            attempts,
            verify,
            safety_frame_attempts,
        )
    return tuple(state.finished[index] for index in sorted(state.finished))


@dataclass(slots=True)
class _BatchState:
    """Mutable queue and independent failure counters of one ``run_jobs`` call.

    ``attempt`` in the queue is the generation number that picks the seed and
    grows on any requeue. Comfy failures and safety rejections are counted
    separately, so one kind of retry never spends the budget of the other.
    """

    queue: deque[tuple[_PlannedItem, int]]
    running: dict[str, _Running]
    finished: dict[int, _JobResult]
    comfy_failures: dict[int, int]
    safety_rejections: dict[int, int]


@dataclass(slots=True)
class _HealthProbe:
    """Periodic ``/system_stats`` check with a consecutive failure limit."""

    client: _ComfyTransport
    interval: float
    fail_limit: int
    clock: Callable[[], float]
    checked_at: float
    failures: int = 0

    def check(self, state: _BatchState) -> None:
        """Check the server once *interval* seconds have passed.

        Args:
            state: Queue whose first running job names the log entry.

        Raises:
            ComfyError: The server failed *fail_limit* checks in a row.
        """
        now = self.clock()
        if now - self.checked_at < self.interval:
            return
        self.checked_at = now
        try:
            self.client.system_stats()
        except ComfyError as ex:
            self.failures += 1
            _log_health_failure(state, self.failures, ex)
            if self.failures >= self.fail_limit:
                raise ComfyError("сервер перестал отвечать") from ex
        else:
            self.failures = 0


@dataclass(frozen=True, slots=True)
class _Running:
    """Prompt queued on the server and awaiting its history."""

    item: _PlannedItem
    attempt: int
    started: float


def _fill(
    client: _ComfyTransport,
    build: Callable[[_PlannedItem, int], dict[str, Any]],
    state: _BatchState,
    attempts: int,
    clock: Callable[[], float],
) -> None:
    """Submit prompts until two are in flight or the queue is empty."""
    while state.queue and len(state.running) < _MAX_IN_FLIGHT:
        item, attempt = state.queue.popleft()
        try:
            prompt_id = client.submit(build(item, attempt))
        except GeneratorError as ex:
            _retry(item, attempt, ex, state, attempts, "постановка")
            continue
        state.running[prompt_id] = _Running(item=item, attempt=attempt, started=clock())


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
    state: _BatchState,
    attempts: int,
    verify: Callable[[_PlannedItem, Path, int], str | None] | None,
    safety_frame_attempts: int,
) -> None:
    """Download a ready result, save it and run the safety check."""
    try:
        output = extract_output(history)
        content = client.download(output)
        if not content:
            raise ComfyError("ComfyUI вернул пустой файл")
        path = directory / job.item.filename
        store(path, content)
    except (GeneratorError, OSError) as ex:
        _retry(job.item, job.attempt, ex, state, attempts, "скачивание")
        return
    if verify is not None:
        try:
            reason = verify(job.item, path, job.attempt)
        except (GeneratorError, OSError) as ex:
            _remove_source(path)
            logger.bind(
                file=job.item.filename,
                attempt=job.attempt,
                error=str(ex),
                error_type=type(ex).__name__,
            ).error("Проверка кадров не удалась — ролик пропущен")
            state.finished[job.item.seq] = _JobResult(item=job.item, error=str(ex))
            return
        if reason is not None:
            _reject(job.item, job.attempt, reason, path, state, safety_frame_attempts)
            return
    state.finished[job.item.seq] = _JobResult(item=job.item, path=path)
    logger.bind(file=path.name, seed=job.item.seed).info("Исходник видео сохранён")


def _reject(
    item: _PlannedItem,
    attempt: int,
    reason: str,
    path: Path,
    state: _BatchState,
    attempts: int,
) -> None:
    """Drop an unsafe source and count the safety rejection of the video."""
    _remove_source(path)
    rejections = state.safety_rejections.get(item.seq, 0) + 1
    state.safety_rejections[item.seq] = rejections
    if rejections < attempts:
        logger.bind(file=item.filename, attempt=attempt, reason=reason).warning(
            "Кадры не прошли проверку — перегенерация с новым seed"
        )
        state.queue.append((item, attempt + 1))
        return
    logger.bind(file=item.filename, attempts=rejections, reason=reason).error(
        "Кадры не прошли проверку — ролик пропущен"
    )
    state.finished[item.seq] = _JobResult(item=item, error=reason)


def _remove_source(path: Path) -> None:
    """Delete a rejected source, tolerating an already removed file."""
    try:
        path.unlink(missing_ok=True)
    except OSError as ex:
        logger.bind(path=str(path), error=str(ex), error_type=type(ex).__name__).warning(
            "Брак кадра не удалён с диска"
        )


def _expire(
    state: _BatchState,
    job_timeout: float,
    clock: Callable[[], float],
) -> None:
    """Raise when a prompt stays without a result for *job_timeout*.

    A hung server is never retried: the caller destroys it and fails the
    remaining videos instead of paying for another attempt on the same host.
    """
    now = clock()
    for job in state.running.values():
        if now - job.started <= job_timeout:
            continue
        error = ComfyError(f"нет результата за {job_timeout:g} с")
        logger.bind(
            file=job.item.filename,
            stage="ожидание",
            error=str(error),
            error_type=type(error).__name__,
        ).error("Задание не отдало результат — сервер считается зависшим")
        raise error


def _log_health_failure(state: _BatchState, failures: int, error: ComfyError) -> None:
    """Log one failed ``/system_stats`` check with the job awaiting a result."""
    running = next(iter(state.running.values()), None)
    logger.bind(
        file=running.item.filename if running is not None else "",
        stage="здоровье",
        failures=failures,
        error=str(error),
        error_type=type(error).__name__,
    ).warning("ComfyUI не ответил на проверку здоровья")


def _retry(
    item: _PlannedItem,
    attempt: int,
    error: Exception,
    state: _BatchState,
    attempts: int,
    stage: str,
) -> None:
    """Count a Comfy failure and requeue the video or record its final failure."""
    failures = state.comfy_failures.get(item.seq, 0) + 1
    state.comfy_failures[item.seq] = failures
    if failures < attempts:
        logger.bind(
            file=item.filename,
            attempt=attempt,
            failures=failures,
            error=str(error),
            error_type=type(error).__name__,
        ).warning(f"Сбой на этапе «{stage}» — повтор")
        state.queue.append((item, attempt + 1))
        return
    logger.bind(
        file=item.filename,
        attempts=failures,
        error=str(error),
        error_type=type(error).__name__,
    ).error(f"Сбой на этапе «{stage}» — ролик пропущен")
    state.finished[item.seq] = _JobResult(item=item, error=str(error))


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
