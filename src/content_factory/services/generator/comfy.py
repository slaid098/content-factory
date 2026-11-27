"""ComfyUI client and generation on one or more rented servers."""

from __future__ import annotations

import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from loguru import logger

from content_factory.config.settings import GeneratorSettings
from content_factory.exceptions.generator import ComfyError, GeneratorError
from content_factory.schemas.generator.server import ServerConnection, ServerInfo
from content_factory.services.generator import media
from content_factory.services.generator.comfy_client import ComfyClient as ComfyClient
from content_factory.services.generator.comfy_client import _ComfyTransport as _ComfyTransport
from content_factory.services.generator.comfy_client import _OutputFile as _OutputFile
from content_factory.services.generator.plan import _PlannedItem
from content_factory.services.generator.profiles import _Profile

_MAX_IN_FLIGHT = 2
_MAX_ATTEMPTS = 3
_HISTORY_POLL = 10.0
_JOB_TIMEOUT = 1800.0


@dataclass(frozen=True, slots=True)
class _JobResult:
    """Outcome of one video: a saved source path or a failure reason."""

    item: _PlannedItem
    path: Path | None = None
    error: str | None = None


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
_SubmitHook = Callable[[_PlannedItem, int, str, str], None]
_DownloadHook = Callable[[_PlannedItem, Path], None]


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


def find_prompt(
    client: _ComfyTransport,
    item: _PlannedItem,
    prompt_id: str | None = None,
) -> str | None:
    """Find the ComfyUI prompt that produces the deterministic file of *item*.

    The workflow names its output after ``item.filename`` without the ``.mp4``
    suffix, so a prompt stays identifiable after a restart even when its
    ``prompt_id`` was never written to the journal. The known *prompt_id* is
    checked first; then the whole history and queue of the server are scanned
    for the output prefix.

    Args:
        client: ComfyUI transport of the server.
        item: Planned video whose output name is searched.
        prompt_id: Prompt id stored in the journal, when present.

    Returns:
        The prompt id found in ``/history`` or ``/queue``, or ``None``.
    """
    prefix = item.filename.removesuffix(".mp4")
    if prompt_id:
        if client.history(prompt_id) is not None:
            return prompt_id
        if _queue_has_prompt(client.queue(), prompt_id):
            return prompt_id
    for candidate, history in client.history_all().items():
        if _history_matches(history, prefix):
            return str(candidate)
    for entry in _queue_entries(client.queue()):
        if _entry_matches(entry, prefix):
            return str(entry[1])
    return None


def _queue_entries(payload: dict[str, Any]) -> list[list[Any]]:
    """Return the running and pending queue entries of a ``/queue`` payload."""
    entries: list[list[Any]] = []
    for key in ("queue_running", "queue_pending"):
        raw = payload.get(key)
        if not isinstance(raw, list):
            continue
        entries.extend(entry for entry in raw if isinstance(entry, list) and len(entry) > 1)
    return entries


def _queue_has_prompt(payload: dict[str, Any], prompt_id: str) -> bool:
    """Return whether a queued entry names *prompt_id*."""
    return any(str(entry[1]) == prompt_id for entry in _queue_entries(payload))


def _entry_matches(entry: list[Any], prefix: str) -> bool:
    """Return whether the workflow of a queue entry writes *prefix*."""
    prompt = entry[2] if len(entry) > 2 else None
    return _has_prefix(prompt, prefix)


def _history_matches(history: object, prefix: str) -> bool:
    """Return whether a history item holds an output with *prefix*."""
    if not isinstance(history, dict):
        return False
    outputs = history.get("outputs")
    if not isinstance(outputs, dict):
        return False
    for output in outputs.values():
        if not isinstance(output, dict):
            continue
        for key in ("gifs", "videos", "images"):
            files = output.get(key)
            if not isinstance(files, list):
                continue
            for item in files:
                if isinstance(item, dict) and str(item.get("filename") or "").startswith(prefix):
                    return True
    return False


def _has_prefix(value: object, prefix: str, depth: int = 0) -> bool:
    """Search a prompt dict for a ``filename_prefix`` equal to *prefix*."""
    if depth > 8:
        return False
    if isinstance(value, dict):
        if str(value.get("filename_prefix") or "") == prefix:
            return True
        return any(_has_prefix(nested, prefix, depth + 1) for nested in value.values())
    if isinstance(value, list):
        return any(_has_prefix(nested, prefix, depth + 1) for nested in value)
    return False


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
    on_submit: Callable[[_PlannedItem, int, str], None] | None = None,
    on_download: Callable[[_PlannedItem, Path], None] | None = None,
    initial: Sequence[tuple[_PlannedItem, int, str]] = (),
    start_attempts: Mapping[int, int] | None = None,
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
        on_submit: Called with the item, attempt and prompt id right after
            the workflow is queued.
        on_download: Called with the item and source path right after a
            verified download.
        initial: Prompts already queued on the server, with their item and
            attempt; they are polled instead of submitted again.
        start_attempts: Attempt number of the first submission per item
            ``seq``; absent items start from the first attempt.

    Returns:
        One result per item, ordered by ``seq``.

    Raises:
        ComfyError: The server stopped answering or a prompt timed out.
    """
    directory.mkdir(parents=True, exist_ok=True)
    saver = store or _store_video
    started_at = clock()
    state = _initial_state(items, initial, start_attempts, started_at)
    health = _HealthProbe(client, health_interval, health_fail_limit, clock, started_at)
    while state.queue or state.running:
        health.check(state)
        _fill(client, build, state, attempts, clock, on_submit)
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
        _fill(client, build, state, attempts, clock, on_submit)
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
            on_download,
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
    on_submit: _SubmitHook | None = None,
    on_download: _DownloadHook | None = None,
    initial: Sequence[tuple[_PlannedItem, int, str]] = (),
    start_attempts: Mapping[int, int] | None = None,
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
        on_submit: Called with the item, attempt, prompt id and contract id.
        on_download: Called with the item and its source path.
        initial: Prompts already queued on the adopted server.
        start_attempts: Attempt number of the first submission per item
            ``seq``.

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
            on_submit,
            on_download,
            initial,
            start_attempts,
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
    on_submit: _SubmitHook | None = None,
    on_download: _DownloadHook | None = None,
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
        on_submit: Called with the item, attempt, prompt id and contract id.
        on_download: Called with the item and its source path.

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
                on_submit=on_submit,
                on_download=on_download,
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
    on_submit: _SubmitHook | None = None,
    on_download: _DownloadHook | None = None,
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
                on_submit,
                on_download,
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
    on_submit: _SubmitHook | None = None,
    on_download: _DownloadHook | None = None,
    initial: Sequence[tuple[_PlannedItem, int, str]] = (),
    start_attempts: Mapping[int, int] | None = None,
) -> tuple[_JobResult, ...]:
    """Run one chunk on a ready server and close its HTTP client."""
    client = client_factory(info.connection)
    contract_id = info.connection.contract_id

    def submit_hook(item: _PlannedItem, attempt: int, prompt_id: str) -> None:
        if on_submit is not None:
            on_submit(item, attempt, prompt_id, contract_id)

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
            on_submit=submit_hook if on_submit is not None else None,
            on_download=on_download,
            initial=initial,
            start_attempts=start_attempts,
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


def _initial_state(
    items: Sequence[_PlannedItem],
    initial: Sequence[tuple[_PlannedItem, int, str]],
    start_attempts: Mapping[int, int] | None,
    started_at: float,
) -> _BatchState:
    """Build the queue and the adopted prompts of one generation call."""
    attempts_by_seq = start_attempts or {}
    return _BatchState(
        queue=deque((item, attempts_by_seq.get(item.seq, 1)) for item in items),
        running={
            prompt_id: _Running(item=item, attempt=attempt, started=started_at)
            for item, attempt, prompt_id in initial
        },
        finished={},
        comfy_failures={},
        safety_rejections={},
    )


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
    on_submit: Callable[[_PlannedItem, int, str], None] | None = None,
) -> None:
    """Submit prompts until two are in flight or the queue is empty."""
    while state.queue and len(state.running) < _MAX_IN_FLIGHT:
        item, attempt = state.queue.popleft()
        try:
            prompt_id = client.submit(build(item, attempt))
        except GeneratorError as ex:
            _retry(item, attempt, ex, state, attempts, "постановка")
            continue
        if on_submit is not None:
            on_submit(item, attempt, prompt_id)
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
    on_download: Callable[[_PlannedItem, Path], None] | None = None,
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
    if on_download is not None:
        on_download(job.item, path)
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
