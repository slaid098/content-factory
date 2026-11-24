"""ComfyUI client and generation on one or more rented servers."""

from __future__ import annotations

import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
from loguru import logger

from content_factory.config.settings import GeneratorSettings
from content_factory.exceptions.generator import ComfyError, GeneratorError
from content_factory.schemas.generator.server import ServerConnection, ServerInfo
from content_factory.services.generator import media
from content_factory.services.generator.plan import _PlannedItem
from content_factory.services.generator.profiles import _Profile

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


class _ServerSession(Protocol):
    """Rented server used by one profile batch."""

    def open(self) -> ServerInfo:
        """Rent the server and return its ready connection."""
        ...

    def destroy(self) -> None:
        """Destroy the server and verify it is gone."""
        ...


_SessionFactory = Callable[[_Profile, GeneratorSettings], _ServerSession]
_ClientFactory = Callable[[ServerConnection], _ComfyTransport]
_Store = Callable[[Path, bytes], None]


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


@dataclass(frozen=True, slots=True)
class _BatchRun:
    """Merged outcome of a batch split across several rented servers."""

    servers: tuple[ServerInfo, ...]
    results: tuple[_JobResult, ...]


@dataclass(frozen=True, slots=True)
class _ChunkOutcome:
    """Outcome of one chunk generated on one rented server."""

    index: int
    items: tuple[_PlannedItem, ...]
    info: ServerInfo | None = None
    results: tuple[_JobResult, ...] = ()
    error: str | None = None


def server_count(count: int, settings: GeneratorSettings) -> int:
    """Return how many working servers a batch of *count* videos needs.

    One server covers ``videos_per_server`` videos; the result is capped by
    ``max_servers`` and never exceeds the number of videos, so a batch of one
    or two videos never rents an extra server.

    Args:
        count: Number of videos in the profile batch, at least one.
        settings: Generator settings with the split limits.

    Returns:
        Number of working servers, at least one.
    """
    by_size = (count + settings.videos_per_server - 1) // settings.videos_per_server
    return min(settings.max_servers, count, by_size)


def split_batch(
    items: Sequence[_PlannedItem],
    settings: GeneratorSettings,
) -> tuple[tuple[_PlannedItem, ...], ...]:
    """Split the plan into contiguous chunks, one per working server.

    Chunks follow the production order and differ in size by at most one, so
    the slowest server does not get a visibly shorter chunk.

    Args:
        items: Planned videos in production order.
        settings: Generator settings with the split limits.

    Returns:
        Non-empty chunks whose videos concatenate into *items*.
    """
    workers = server_count(len(items), settings)
    base, extra = divmod(len(items), workers)
    chunks: list[tuple[_PlannedItem, ...]] = []
    start = 0
    for index in range(workers):
        stop = start + base + (1 if index < extra else 0)
        chunks.append(tuple(items[start:stop]))
        start = stop
    return tuple(chunks)


def run_server(
    session: _ServerSession,
    client_factory: _ClientFactory,
    items: Sequence[_PlannedItem],
    build: Callable[[_PlannedItem, int], dict[str, Any]],
    directory: Path,
    *,
    label: str,
    settings: GeneratorSettings,
    store: _Store | None = None,
    verify: Callable[[_PlannedItem, Path, int], str | None] | None = None,
) -> tuple[ServerInfo, tuple[_JobResult, ...]]:
    """Rent one server, generate every item and destroy it before returning.

    The teardown is verified even when the generation already failed, so a
    paid server never outlives the call. The failure itself is re-raised after
    the teardown.

    Args:
        session: Rental session of the profile.
        client_factory: ComfyUI client factory.
        items: Planned videos generated on this server.
        build: Workflow builder called with the item and the attempt number.
        directory: Directory that receives downloaded silent sources.
        label: Profile name used in teardown logs.
        settings: Generator settings with timeouts and safety attempts.
        store: Saver of downloaded bytes; defaults to the ffmpeg cut.
        verify: Safety check of a saved source; returns a defect reason.

    Returns:
        Ready server info and one result per item, ordered by ``seq``.
    """
    info: ServerInfo | None = None
    try:
        info = session.open()
        results = _generate(
            client_factory,
            info,
            items,
            build,
            directory,
            settings,
            store,
            verify,
        )
    except Exception as ex:  # noqa: BLE001 — teardown must run before re-raising
        _log_chunk(0, items, info, str(ex), type(ex).__name__)
        raise
    finally:
        _destroy(session, label)
    _log_chunk(0, items, info, None, None)
    return info, results


def run_servers(
    profile: _Profile,
    settings: GeneratorSettings,
    chunks: Sequence[Sequence[_PlannedItem]],
    session_factory: _SessionFactory,
    client_factory: _ClientFactory,
    build: Callable[[_PlannedItem, int], dict[str, Any]],
    directory: Path,
    *,
    store: _Store | None = None,
    verify: Callable[[_PlannedItem, Path, int], str | None] | None = None,
) -> _BatchRun:
    """Generate every chunk on its own server, one rent race at a time.

    Only one session rents at a time: its internal pool already fills
    ``rent_pool_size`` candidates, so overlapping rents would multiply paid
    instances. A ready server leaves the lock and generates its chunk while
    the next one rents. A failed chunk fails only its own videos; the other
    chunks continue. Every session is destroyed before the call returns,
    including the failure path.

    Args:
        profile: Loaded profile of the batch.
        settings: Generator settings with the split limits and timeouts.
        chunks: Contiguous chunks of the plan, one per server.
        session_factory: Rental factory; tests inject a fake server.
        client_factory: ComfyUI client factory; tests inject a fake client.
        build: Workflow builder called with the item and the attempt number.
        directory: Directory that receives downloaded silent sources.
        store: Saver of downloaded bytes; defaults to the ffmpeg cut.
        verify: Safety check of a saved source; returns a defect reason.

    Returns:
        First ready servers in chunk order and one result per planned video,
        ordered by ``seq``; videos of a failed chunk carry its error.
    """
    rent_lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=min(len(chunks), settings.max_servers)) as pool:
        futures = [
            pool.submit(
                _run_chunk,
                index,
                tuple(chunk),
                profile=profile,
                settings=settings,
                session_factory=session_factory,
                client_factory=client_factory,
                build=build,
                directory=directory,
                rent_lock=rent_lock,
                store=store,
                verify=verify,
            )
            for index, chunk in enumerate(chunks)
        ]
        outcomes = [future.result() for future in futures]
    return _merge_chunks(outcomes)


def _run_chunk(
    index: int,
    items: tuple[_PlannedItem, ...],
    *,
    profile: _Profile,
    settings: GeneratorSettings,
    session_factory: _SessionFactory,
    client_factory: _ClientFactory,
    build: Callable[[_PlannedItem, int], dict[str, Any]],
    directory: Path,
    rent_lock: threading.Lock,
    store: _Store | None,
    verify: Callable[[_PlannedItem, Path, int], str | None] | None,
) -> _ChunkOutcome:
    """Rent one server and generate one chunk; a failure stays in its chunk."""
    session = session_factory(profile, settings)
    info: ServerInfo | None = None
    results: tuple[_JobResult, ...] = ()
    error: str | None = None
    try:
        try:
            with rent_lock:
                info = session.open()
            results = _generate(
                client_factory,
                info,
                items,
                build,
                directory,
                settings,
                store,
                verify,
            )
        finally:
            _destroy(session, profile.name)
    except Exception as ex:  # noqa: BLE001 — one chunk must not cancel the batch
        error = str(ex)
        _log_chunk(index, items, info, error, type(ex).__name__)
        results = ()
    else:
        _log_chunk(index, items, info, None, None)
    return _ChunkOutcome(index=index, items=items, info=info, results=results, error=error)


def _generate(
    client_factory: _ClientFactory,
    info: ServerInfo,
    items: Sequence[_PlannedItem],
    build: Callable[[_PlannedItem, int], dict[str, Any]],
    directory: Path,
    settings: GeneratorSettings,
    store: _Store | None,
    verify: Callable[[_PlannedItem, Path, int], str | None] | None,
) -> tuple[_JobResult, ...]:
    """Run one chunk on a ready server and close its HTTP client."""
    client = client_factory(info.connection)
    try:
        return run_jobs(
            client,
            items,
            build,
            directory,
            store=store,
            verify=verify,
            safety_frame_attempts=settings.safety_frame_attempts,
            job_timeout=settings.comfy_job_timeout,
            health_interval=settings.comfy_health_interval,
        )
    finally:
        client.close()


def _merge_chunks(outcomes: Sequence[_ChunkOutcome]) -> _BatchRun:
    """Merge chunk outcomes and name every video of a failed chunk."""
    results: list[_JobResult] = []
    servers: list[ServerInfo] = []
    for outcome in outcomes:
        if outcome.info is not None:
            servers.append(outcome.info)
        if outcome.error is not None:
            results.extend(_JobResult(item=item, error=outcome.error) for item in outcome.items)
        else:
            results.extend(outcome.results)
    results.sort(key=lambda result: result.item.seq)
    return _BatchRun(servers=tuple(servers), results=tuple(results))


def _log_chunk(
    index: int,
    items: Sequence[_PlannedItem],
    info: ServerInfo | None,
    error: str | None,
    error_type: str | None,
) -> None:
    """Log the server, its chunk size and the outcome without secrets."""
    connection = info.connection if info is not None else None
    context = logger.bind(
        contract_id=connection.contract_id if connection is not None else None,
        gpu=connection.gpu if connection is not None else None,
        chunk=index + 1,
        videos=len(items),
        outcome="failed" if error is not None else "done",
    )
    if error is None:
        context.info("Кусок партии сгенерирован")
        return
    context.bind(error=error, error_type=error_type).error("Кусок партии не сгенерирован")


def _destroy(session: _ServerSession, label: str) -> None:
    """Destroy the server without masking an already active exception."""
    active = sys.exc_info()[0] is not None
    try:
        session.destroy()
    except Exception as ex:  # noqa: BLE001 — teardown must not hide the original error
        logger.bind(profile=label, error=str(ex), error_type=type(ex).__name__).critical(
            "Сервер не уничтожен — погасите вручную"
        )
        if not active:
            raise


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
