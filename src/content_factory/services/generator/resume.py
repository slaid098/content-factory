"""Resume unfinished generator runs after a process restart.

The journal is the source of truth: a run and its plan are written before the
first network call, every item step updates its row, and a run closes only
after its servers are destroyed. This module rebuilds the missing parts from
those rows instead of starting a second batch.
"""

from __future__ import annotations

import random
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from content_factory.config.settings import GeneratorSettings
from content_factory.constants import DB_DIR, DB_FILENAME, PROFILES_DIR
from content_factory.db import generator_runs, generator_servers
from content_factory.db.connection import Connection
from content_factory.db.run_journal import RunJournalWriter
from content_factory.db.server_journal import ServerJournal
from content_factory.exceptions.generator import (
    GeneratorError,
    JournalError,
    ProfileError,
    RentError,
)
from content_factory.schemas.content.delivery import ContentType
from content_factory.schemas.generator.journal import JournalPlanItem
from content_factory.schemas.generator.pipeline import Layer, PipelineContext
from content_factory.schemas.generator.run import (
    RunItem,
    RunReport,
    RunServer,
    RunStatus,
    RunStep,
    RunTotals,
)
from content_factory.schemas.generator.server import ServerConnection, ServerInfo
from content_factory.schemas.generator.text import TextWriter
from content_factory.services import production
from content_factory.services.generator import VastProvider, comfy, profiles, safety, service
from content_factory.services.generator.plan import _PlannedItem, attempt_seed, planned_item
from content_factory.services.generator.profiles import (
    _Profile,
    load_profile,
    profile_slug,
)
from content_factory.services.generator.vast import (
    VastSession,
    destroy_verified,
)
from content_factory.services.generator.workflow import build_workflow
from content_factory.services.production.card import CardProducer
from content_factory.utils.run_id import profile_run_dir

_FINAL_DIR = "final"
_SOURCE_DIR = "source"


@dataclass(frozen=True, slots=True)
class RunOutcome:
    """Result of resuming one run."""

    run_id: str
    status: str
    generated: int
    failed: int


class _LostServer(GeneratorError):
    """A server journal row of the run vanished from the Vast.ai account."""


@dataclass(frozen=True, slots=True)
class _LiveServer:
    """One ready journal row and the ComfyUI connection it names."""

    row: generator_servers.GeneratorServerRow
    info: ServerInfo


class _AdoptedSession:
    """A ready server that must not be rented again or destroyed by ``run_server``."""

    def __init__(self, info: ServerInfo) -> None:
        """Store the connection of the adopted server."""
        self._info = info

    def open(self) -> ServerInfo:
        """Return the already known ready server."""
        return self._info

    def destroy(self) -> None:
        """Leave the server alive; the run destroys it after post-processing."""
        return None


def resume_unfinished(
    connection: Connection,
    *,
    data_dir: Path,
    settings: GeneratorSettings,
    provider: VastProvider | None = None,
    journal: RunJournalWriter | None = None,
    client_factory: Callable[[ServerConnection], comfy._ComfyTransport] = comfy.ComfyClient,
    text_writer: TextWriter | None = None,
    checker: safety._Checker | None = None,
    rng: random.Random | None = None,
) -> list[RunOutcome]:
    """Resume every unfinished run, closing each one with a final status.

    A run that cannot be resumed is marked ``failed`` with the reason and does
    not stop the next run. A run without any journal item gets its plan rebuilt
    from the stored profiles, count and content type before any network call.

    Args:
        connection: Open connection used to list runs and items.
        data_dir: Root ``app_data`` directory with profiles and the database.
        settings: Generator settings with the output directory and timeouts.
        provider: Vast.ai client; when omitted, existing servers are trusted
            and no server is destroyed.
        journal: Run journal writer; the database in *data_dir* when omitted.
        client_factory: ComfyUI transport factory.
        text_writer: Text model used by post-processing; a default is built.
        checker: Safety checker; defaults to the configured Clef client.
        rng: Random source of a rebuilt plan and text cards.

    Returns:
        One outcome per unfinished run, in journal order.
    """
    writer = journal or RunJournalWriter(data_dir / DB_DIR / DB_FILENAME)
    randomizer = rng or random.Random()  # noqa: S311 — variety, not secrets
    outcomes: list[RunOutcome] = []
    for run in generator_runs.list_unfinished_runs(connection):
        outcomes.append(
            _resume_one(
                connection,
                run,
                data_dir=data_dir,
                settings=settings,
                provider=provider,
                journal=writer,
                client_factory=client_factory,
                text_writer=text_writer,
                checker=checker,
                rng=randomizer,
            )
        )
    return outcomes


def _resume_one(
    connection: Connection,
    run: generator_runs.GeneratorRunRow,
    *,
    data_dir: Path,
    settings: GeneratorSettings,
    provider: VastProvider | None,
    journal: RunJournalWriter,
    client_factory: Callable[[ServerConnection], comfy._ComfyTransport],
    text_writer: TextWriter | None,
    checker: safety._Checker | None,
    rng: random.Random,
) -> RunOutcome:
    """Resume one run, marking it failed when it cannot continue."""
    try:
        loaded = _load_profiles(run, data_dir / PROFILES_DIR)
        _ensure_plan(connection, run, loaded, data_dir, settings, journal, rng)
        rows = generator_runs.list_items(connection, run.run_id)
        _resume_content(
            connection,
            run,
            loaded,
            rows,
            data_dir=data_dir,
            settings=settings,
            provider=provider,
            journal=journal,
            client_factory=client_factory,
            text_writer=text_writer,
            checker=checker,
            rng=rng,
        )
        rows = generator_runs.list_items(connection, run.run_id)
        _write_reports(connection, run, loaded, rows, settings)
        status = _status(rows)
        journal.finish_run(run.run_id, status)
        logger.bind(run_id=run.run_id, status=status, items=len(rows)).info(
            "Ран генератора восстановлен"
        )
        return _outcome(run.run_id, status, rows)
    except (GeneratorError, OSError, ValueError) as ex:
        logger.bind(run_id=run.run_id, error=str(ex), error_type=type(ex).__name__).error(
            "Ран генератора не восстановлен — помечен failed"
        )
        _fail_run(connection, run, journal, str(ex))
        rows = generator_runs.list_items(connection, run.run_id)
        return _outcome(run.run_id, generator_runs.FAILED, rows)
    finally:
        _destroy_run_servers(connection, run, provider)


def _resume_content(
    connection: Connection,
    run: generator_runs.GeneratorRunRow,
    loaded: Sequence[_Profile],
    rows: Sequence[generator_runs.GeneratorItemRow],
    *,
    data_dir: Path,
    settings: GeneratorSettings,
    provider: VastProvider | None,
    journal: RunJournalWriter,
    client_factory: Callable[[ServerConnection], comfy._ComfyTransport],
    text_writer: TextWriter | None,
    checker: safety._Checker | None,
    rng: random.Random,
) -> None:
    """Resume the items of every profile of the run."""
    needs_video = any(
        row.type == ContentType.VIDEO.value
        and row.status not in generator_runs.TERMINAL_ITEM_STATUSES
        for row in rows
    )
    active_checker = _active_checker(settings, checker) if needs_video else None
    for profile in loaded:
        own = [row for row in rows if row.profile == profile.name]
        _resume_photos(profile, own, run, data_dir, settings, journal, text_writer, rng)
        _resume_videos(
            connection,
            run,
            profile,
            own,
            data_dir=data_dir,
            settings=settings,
            provider=provider,
            journal=journal,
            client_factory=client_factory,
            text_writer=text_writer,
            checker=active_checker,
            rng=rng,
        )


def _resume_photos(
    profile: _Profile,
    rows: Sequence[generator_runs.GeneratorItemRow],
    run: generator_runs.GeneratorRunRow,
    data_dir: Path,
    settings: GeneratorSettings,
    journal: RunJournalWriter,
    text_writer: TextWriter | None,
    rng: random.Random,
) -> None:
    """Finish photo items locally; no server takes part in this layer."""
    photo_rows = [row for row in rows if row.type == ContentType.PHOTO.value]
    if not photo_rows:
        return
    context = _context(profile, run, data_dir, settings, journal, rng)
    producer = CardProducer(writer=text_writer)
    run_dir = profile_run_dir(
        settings.output_dir, profile.language, profile_slug(profile), run.run_id
    )
    for row in photo_rows:
        if row.status in generator_runs.TERMINAL_ITEM_STATUSES:
            continue
        producer.produce_one(profile, row.file, run_dir, context)


def _resume_videos(
    connection: Connection,
    run: generator_runs.GeneratorRunRow,
    profile: _Profile,
    rows: Sequence[generator_runs.GeneratorItemRow],
    *,
    data_dir: Path,
    settings: GeneratorSettings,
    provider: VastProvider | None,
    journal: RunJournalWriter,
    client_factory: Callable[[ServerConnection], comfy._ComfyTransport],
    text_writer: TextWriter | None,
    checker: safety._Checker | None,
    rng: random.Random,
) -> None:
    """Resume the video layer of one profile on its existing servers."""
    video_rows = [row for row in rows if row.type == ContentType.VIDEO.value]
    if not video_rows:
        return
    layer = _layer_for(profile, video_rows[0])
    run_dir = profile_run_dir(
        settings.output_dir, profile.language, profile_slug(profile), run.run_id
    )
    report = _report(
        run,
        profile,
        video_rows,
        _server_block(connection, run),
        profile.production,
        run_dir,
    )
    results: list[comfy._JobResult] = []
    pending: list[generator_runs.GeneratorItemRow] = []
    for row in video_rows:
        if row.status in generator_runs.TERMINAL_ITEM_STATUSES:
            continue
        source = _source(row)
        if source is not None:
            results.append(comfy._JobResult(item=_planned(profile, row), path=source))
            continue
        if _had_source(row):
            journal.requeue(run.run_id, profile.name, row.file)
        pending.append(row)
    if pending:
        results.extend(
            _run_remote(
                connection,
                run,
                profile,
                pending,
                run_dir,
                data_dir=data_dir,
                settings=settings,
                provider=provider,
                journal=journal,
                client_factory=client_factory,
                checker=checker,
                rng=rng,
            )
        )
    service._postprocess(
        profile,
        layer,
        tuple(results),
        run_dir,
        report,
        settings,
        text_writer,
        rng,
        data_dir,
        journal,
    )


def _run_remote(
    connection: Connection,
    run: generator_runs.GeneratorRunRow,
    profile: _Profile,
    rows: Sequence[generator_runs.GeneratorItemRow],
    run_dir: Path,
    *,
    data_dir: Path,
    settings: GeneratorSettings,
    provider: VastProvider | None,
    journal: RunJournalWriter,
    client_factory: Callable[[ServerConnection], comfy._ComfyTransport],
    checker: safety._Checker | None,
    rng: random.Random,
) -> list[comfy._JobResult]:
    """Find, wait for or resubmit the prompts of missing video items."""
    items = [_planned(profile, row) for row in rows]
    plans = {row.file: item for row, item in zip(rows, items, strict=True)}
    servers = _live_servers(connection, run, provider)
    if not servers and _has_bound_server(connection, run):
        raise _LostServer(f"сервер рана {run.run_id} исчез из Vast.ai")
    if not servers:
        return _rent_and_generate(
            run,
            profile,
            rows,
            plans,
            run_dir,
            data_dir=data_dir,
            settings=settings,
            provider=provider,
            journal=journal,
            client_factory=client_factory,
            checker=checker,
            rng=rng,
        )
    return _generate_on_servers(
        run,
        profile,
        rows,
        plans,
        run_dir,
        servers,
        settings=settings,
        journal=journal,
        client_factory=client_factory,
        checker=checker,
        rng=rng,
    )


def _generate_on_servers(
    run: generator_runs.GeneratorRunRow,
    profile: _Profile,
    rows: Sequence[generator_runs.GeneratorItemRow],
    plans: dict[str, _PlannedItem],
    run_dir: Path,
    servers: Sequence[_LiveServer],
    *,
    settings: GeneratorSettings,
    journal: RunJournalWriter,
    client_factory: Callable[[ServerConnection], comfy._ComfyTransport],
    checker: safety._Checker | None,
    rng: random.Random,
) -> list[comfy._JobResult]:
    """Generate every pending item on the server that already owns it."""
    by_id = {server.row.id: server for server in servers}
    assigned: dict[str, list[generator_runs.GeneratorItemRow]] = {sid: [] for sid in by_id}
    unassigned: list[generator_runs.GeneratorItemRow] = []
    for row in rows:
        if row.server_id is None:
            unassigned.append(row)
        elif row.server_id in assigned:
            assigned[row.server_id].append(row)
        else:
            raise _LostServer(f"сервер {row.server_id} рана {run.run_id} исчез из Vast.ai")
    for index, row in enumerate(unassigned):
        target = servers[index % len(servers)].row.id
        assigned[target].append(row)
    builder = _builder(profile, rng)
    verifier = _verifier(checker)
    attempts = {plans[row.file].seq: (row.attempt or 0) + 1 for row in rows}
    results: list[comfy._JobResult] = []
    for server in servers:
        own = assigned.get(server.row.id, [])
        if not own:
            continue
        adopted = _adopted(server.info, own, plans, client_factory, journal, run.run_id)
        adopted_names = {item.filename for item, _, _ in adopted}
        try:
            _, generated = comfy.run_server(
                _AdoptedSession(server.info),
                client_factory,
                [plans[row.file] for row in own if row.file not in adopted_names],
                builder,
                run_dir / _SOURCE_DIR,
                label=profile.name,
                settings=settings,
                verify=verifier,
                on_submit=_submit_hook(journal, run.run_id),
                on_download=_download_hook(journal, run.run_id),
                initial=adopted,
                start_attempts=attempts,
            )
        except GeneratorError as ex:
            logger.bind(
                run_id=run.run_id,
                contract_id=server.row.contract_id,
                error=str(ex),
                error_type=type(ex).__name__,
            ).error("Сервер рана не продолжил генерацию — его элементы failed")
            results.extend(comfy._JobResult(item=plans[row.file], error=str(ex)) for row in own)
            continue
        results.extend(generated)
    return results


def _rent_and_generate(
    run: generator_runs.GeneratorRunRow,
    profile: _Profile,
    rows: Sequence[generator_runs.GeneratorItemRow],
    plans: dict[str, _PlannedItem],
    run_dir: Path,
    *,
    data_dir: Path,
    settings: GeneratorSettings,
    provider: VastProvider | None,
    journal: RunJournalWriter,
    client_factory: Callable[[ServerConnection], comfy._ComfyTransport],
    checker: safety._Checker | None,
    rng: random.Random,
) -> list[comfy._JobResult]:
    """Rent the first server of a run that never got one and generate there."""
    session = VastSession(
        profile,
        settings,
        provider=provider,
        journal=ServerJournal(data_dir / DB_DIR / DB_FILENAME),
        run_id=run.run_id,
    )
    items = [plans[row.file] for row in rows]
    attempts = {item.seq: (row.attempt or 0) + 1 for row, item in zip(rows, items, strict=True)}
    try:
        _, results = comfy.run_server(
            session,
            client_factory,
            items,
            _builder(profile, rng),
            run_dir / _SOURCE_DIR,
            label=profile.name,
            settings=settings,
            verify=_verifier(checker),
            on_submit=_submit_hook(journal, run.run_id),
            on_download=_download_hook(journal, run.run_id),
            start_attempts=attempts,
        )
    except GeneratorError as ex:
        logger.bind(run_id=run.run_id, error=str(ex), error_type=type(ex).__name__).error(
            "Новый сервер рана не смог продолжить генерацию"
        )
        return [comfy._JobResult(item=item, error=str(ex)) for item in items]
    return list(results)


def _builder(
    profile: _Profile,
    rng: random.Random,
) -> Callable[[_PlannedItem, int], dict[str, Any]]:
    """Build a workflow for one resumed item with the shared random source."""
    lock = threading.Lock()

    def build(item: _PlannedItem, attempt: int) -> dict[str, Any]:
        with lock:
            prompt = profiles.build_prompt(profile, item.look, rng)
        return build_workflow(profile, item, prompt, seed=attempt_seed(item.seed, attempt))

    return build


def _verifier(
    checker: safety._Checker | None,
) -> Callable[[_PlannedItem, Path, int], str | None] | None:
    """Build the source check used by a resumed remote phase."""
    if checker is None:
        return None

    def verify(item: _PlannedItem, source: Path, attempt: int) -> str | None:
        return checker.verify(item, source, attempt)

    return verify


def _submit_hook(
    journal: RunJournalWriter,
    run_id: str,
) -> Callable[[_PlannedItem, int, str, str], None]:
    """Journal a resubmitted prompt immediately after ComfyUI accepts it."""

    def submitted(item: _PlannedItem, attempt: int, prompt_id: str, contract_id: str) -> None:
        journal.submitted(run_id, item.profile, item.filename, prompt_id, attempt, contract_id)

    return submitted


def _download_hook(
    journal: RunJournalWriter,
    run_id: str,
) -> Callable[[_PlannedItem, Path], None]:
    """Journal a downloaded source immediately after it is stored."""

    def downloaded(item: _PlannedItem, source: Path) -> None:
        journal.downloaded(run_id, item.profile, item.filename, str(source))

    return downloaded


def _adopted(
    info: ServerInfo,
    rows: Sequence[generator_runs.GeneratorItemRow],
    plans: dict[str, _PlannedItem],
    client_factory: Callable[[ServerConnection], comfy._ComfyTransport],
    journal: RunJournalWriter,
    run_id: str,
) -> list[tuple[_PlannedItem, int, str]]:
    """Find prompts that still run or already finished on the adopted server."""
    client = client_factory(info.connection)
    adopted: list[tuple[_PlannedItem, int, str]] = []
    try:
        for row in rows:
            item = plans[row.file]
            prompt_id = comfy.find_prompt(client, item, row.prompt_id)
            if prompt_id is None:
                continue
            attempt = row.attempt or 1
            adopted.append((item, attempt, prompt_id))
            if row.prompt_id != prompt_id or row.attempt != attempt:
                journal.submitted(
                    run_id, row.profile, row.file, prompt_id, attempt, info.connection.contract_id
                )
            logger.bind(
                file=row.file,
                prompt_id=prompt_id,
                attempt=attempt,
            ).info("Prompt рана найден на сервере")
    finally:
        client.close()
    return adopted


def _planned(
    profile: _Profile,
    row: generator_runs.GeneratorItemRow,
) -> _PlannedItem:
    """Rebuild one planned video from its journal row."""
    return planned_item(
        profile,
        row.file,
        look_id=row.look_id,
        seed=row.seed,
        frames=row.frames,
        seq=_seq(row.file),
    )


def _seq(filename: str) -> int:
    """Return the one-based sequence stored in a deterministic file name."""
    try:
        return int(Path(filename).stem.rsplit("_", 1)[-1])
    except (ValueError, IndexError):
        return 1


def _source(row: generator_runs.GeneratorItemRow) -> Path | None:
    """Return the recorded source file when it still exists."""
    if not row.source_path:
        return None
    path = Path(row.source_path)
    return path if path.is_file() else None


def _had_source(row: generator_runs.GeneratorItemRow) -> bool:
    """Return whether the journal recorded a source that is now gone."""
    return bool(row.source_path) and row.status in (
        generator_runs.DOWNLOADED,
        generator_runs.POSTPROCESSING,
    )


def _load_profiles(
    run: generator_runs.GeneratorRunRow,
    root: Path,
) -> list[_Profile]:
    """Load the profiles of a run with the content type stored in its row."""
    try:
        selected = None if run.content_type is None else ContentType(run.content_type)
    except ValueError as ex:
        raise ProfileError(f"в журнале рана неизвестный content_type {run.content_type!r}") from ex
    loaded: list[_Profile] = []
    for name in run.profiles:
        profile = load_profile(name, root)
        if selected is not None:
            profile = replace(
                profile,
                production=production.filter_recipe(profile.name, profile.production, selected),
            )
        loaded.append(profile)
    return loaded


def _ensure_plan(
    connection: Connection,
    run: generator_runs.GeneratorRunRow,
    loaded: Sequence[_Profile],
    data_dir: Path,
    settings: GeneratorSettings,
    journal: RunJournalWriter,
    rng: random.Random,
) -> None:
    """Rebuild and journal a plan that was lost before the first network call."""
    existing = generator_runs.list_items(connection, run.run_id)
    expected = run.count * sum(len(profile.production) for profile in loaded)
    if len(existing) >= expected:
        return
    context = PipelineContext(
        profiles=tuple(loaded),
        count=run.count,
        data_dir=data_dir,
        settings=settings,
        run_id=run.run_id,
        rng=rng,
        now=_started(run),
    )
    items: list[JournalPlanItem] = []
    registry = production.default_producers()
    for profile in loaded:
        for layer in profile.production:
            producer = registry.resolve(layer.producer)
            for artifact in producer.plan_layer(layer, context):
                items.append(_plan_item(layer, artifact))
    journal.plan(run.run_id, items)
    logger.bind(run_id=run.run_id, items=len(items)).info("План рана восстановлен из профилей")


def _plan_item(layer: Layer, artifact: Any) -> JournalPlanItem:
    """Convert one planned artifact into a journal row."""
    return JournalPlanItem(
        profile=artifact.profile,
        file=artifact.item,
        producer=layer.producer,
        type=layer.output.value,
        look_id=_text(artifact.meta.get("look_id")),
        seed=_integer(artifact.meta.get("seed")),
        frames=_integer(artifact.meta.get("frames")),
    )


def _context(
    profile: _Profile,
    run: generator_runs.GeneratorRunRow,
    data_dir: Path,
    settings: GeneratorSettings,
    journal: RunJournalWriter,
    rng: random.Random,
) -> PipelineContext:
    """Build a minimal pipeline context for a single resumed profile."""
    return PipelineContext(
        profiles=(profile,),
        count=run.count,
        data_dir=data_dir,
        settings=settings,
        run_id=run.run_id,
        rng=rng,
        journal=journal,
        now=_started(run),
    )


def _layer_for(
    profile: _Profile,
    row: generator_runs.GeneratorItemRow,
) -> Layer:
    """Return the recipe layer of one journal item.

    Raises:
        GeneratorError: The recipe no longer has that producer and type.
    """
    for layer in profile.production:
        if layer.producer == row.producer and layer.output.value == row.type:
            return layer
    raise GeneratorError(
        f"в рецепте профиля {profile.name} нет слоя {row.producer!r} типа {row.type!r}"
    )


def _live_servers(
    connection: Connection,
    run: generator_runs.GeneratorRunRow,
    provider: VastProvider | None,
) -> list[_LiveServer]:
    """Return ready servers of the run that still exist in the Vast.ai account."""
    rows = [
        row
        for row in generator_servers.list_for_run(connection, run.run_id)
        if row.status == generator_servers.READY and row.contract_id and row.ip and row.port
    ]
    if not rows:
        return []
    present = _present_contracts(provider)
    servers: list[_LiveServer] = []
    for row in rows:
        if present is not None and row.contract_id not in present:
            generator_servers.mark_lost(connection, row.id, _now(), "инстанс исчез из Vast.ai")
            connection.commit()
            continue
        info = ServerInfo(
            connection=ServerConnection(
                contract_id=row.contract_id or "",
                ip=row.ip or "",
                port=row.port or 0,
                gpu=row.gpu or "",
                image=row.image or "",
            ),
            cost_per_hour=row.cost_per_hour,
        )
        servers.append(_LiveServer(row, info))
    return servers


def _has_bound_server(
    connection: Connection,
    run: generator_runs.GeneratorRunRow,
) -> bool:
    """Return whether the run ever had a contract, so a missing one is a loss."""
    return any(
        row.contract_id is not None
        for row in generator_servers.list_for_run(connection, run.run_id)
    )


def _present_contracts(provider: VastProvider | None) -> set[str] | None:
    """Return the contract ids of the account, or ``None`` when unavailable."""
    if provider is None:
        return None
    try:
        instances = provider.list_instances()
    except RentError as ex:
        logger.bind(error=str(ex), error_type=type(ex).__name__).warning(
            "Список инстансов Vast.ai недоступен — серверы рана проверяются подключением"
        )
        return None
    return {
        contract_id
        for instance in instances
        if (contract_id := str(instance.get("id") or "").strip())
    }


def _destroy_run_servers(
    connection: Connection,
    run: generator_runs.GeneratorRunRow,
    provider: VastProvider | None,
) -> None:
    """Destroy every remaining server of the closed run with verification."""
    for row in generator_servers.list_for_run(connection, run.run_id):
        if row.status not in generator_servers.UNFINISHED_STATUSES:
            continue
        if row.contract_id is None:
            generator_servers.mark_lost(connection, row.id, _now(), "contract_id не появился")
            connection.commit()
            continue
        if provider is None:
            continue
        generator_servers.mark_destroying(connection, row.id, _now())
        connection.commit()
        try:
            destroy_verified(provider, row.contract_id)
        except RentError as ex:
            generator_servers.mark_destroying(connection, row.id, _now(), str(ex))
            connection.commit()
            logger.bind(
                contract_id=row.contract_id,
                run_id=run.run_id,
                error=str(ex),
            ).error("Сервер рана не уничтожен — следующий тик recovery повторит")
            continue
        generator_servers.mark_destroyed(connection, row.id, _now())
        connection.commit()


def _write_reports(
    connection: Connection,
    run: generator_runs.GeneratorRunRow,
    loaded: Sequence[_Profile],
    rows: Sequence[generator_runs.GeneratorItemRow],
    settings: GeneratorSettings,
) -> None:
    """Rebuild ``run.json`` of every profile from the journal rows."""
    server = _server_block(connection, run)
    for profile in loaded:
        own = [row for row in rows if row.profile == profile.name]
        run_dir = profile_run_dir(
            settings.output_dir, profile.language, profile_slug(profile), run.run_id
        )
        report = _report(run, profile, own, server, profile.production, run_dir)
        _write(report, run_dir)


def _report(
    run: generator_runs.GeneratorRunRow,
    profile: _Profile,
    rows: Sequence[generator_runs.GeneratorItemRow],
    server: RunServer | None,
    recipe: Sequence[Layer],
    run_dir: Path,
) -> RunReport:
    """Build the report of one profile from journal rows."""
    items = [_run_item(profile, row, recipe) for row in rows]
    done = [row for row in rows if row.status == generator_runs.ITEM_DONE]
    failed = [row for row in rows if row.status == generator_runs.ITEM_FAILED]
    final_dir = run_dir / _FINAL_DIR
    size = sum(path.stat().st_size for row in done if (path := final_dir / row.file).is_file())
    return RunReport(
        run_id=run.run_id,
        started_at=_started(run),
        finished_at=datetime.now(UTC),
        status=RunStatus.FAILED if failed else RunStatus.DONE,
        profiles=[profile.name],
        count=run.count,
        production=[
            RunStep(
                producer=layer.producer,
                type=ContentType(layer.output.value),
                qa=list(layer.checks),
            )
            for layer in recipe
        ],
        server=server,
        items=items,
        totals=RunTotals(generated=len(done), failed=len(failed), bytes=size),
    )


def _run_item(
    profile: _Profile,
    row: generator_runs.GeneratorItemRow,
    recipe: Sequence[Layer],
) -> RunItem:
    """Build one report item from its journal row."""
    layer = next(
        (
            entry
            for entry in recipe
            if entry.producer == row.producer and entry.output.value == row.type
        ),
        None,
    )
    return RunItem(
        file=row.file,
        profile=profile.name,
        look_id=row.look_id,
        seed=row.seed,
        frames=row.frames,
        status=_item_status(row.status),
        error=row.error,
        producer=row.producer,
        type=ContentType(row.type),
        qa=list(layer.checks) if layer is not None else [],
    )


def _server_block(
    connection: Connection,
    run: generator_runs.GeneratorRunRow,
) -> RunServer | None:
    """Return the server block of the first row that names a contract."""
    for row in generator_servers.list_for_run(connection, run.run_id):
        if row.contract_id is None:
            continue
        return RunServer(
            contract_id=row.contract_id,
            ip=row.ip,
            gpu=row.gpu,
            image=row.image,
            cost_per_hour=row.cost_per_hour,
        )
    return None


def _item_status(status: str) -> RunStatus:
    """Map a journal item status to the report status."""
    if status == generator_runs.ITEM_DONE:
        return RunStatus.DONE
    if status == generator_runs.ITEM_FAILED:
        return RunStatus.FAILED
    return RunStatus.RUNNING


def _status(rows: Sequence[generator_runs.GeneratorItemRow]) -> str:
    """Return the run status implied by its items."""
    return (
        generator_runs.FAILED
        if any(row.status == generator_runs.ITEM_FAILED for row in rows)
        else generator_runs.DONE
    )


def _outcome(
    run_id: str,
    status: str,
    rows: Sequence[generator_runs.GeneratorItemRow],
) -> RunOutcome:
    """Summarize one resumed run."""
    return RunOutcome(
        run_id=run_id,
        status=status,
        generated=sum(1 for row in rows if row.status == generator_runs.ITEM_DONE),
        failed=sum(1 for row in rows if row.status == generator_runs.ITEM_FAILED),
    )


def _fail_run(
    connection: Connection,
    run: generator_runs.GeneratorRunRow,
    journal: RunJournalWriter,
    reason: str,
) -> None:
    """Mark every unfinished item and the run itself failed with *reason*."""
    try:
        for row in generator_runs.list_items(connection, run.run_id):
            if row.status not in generator_runs.TERMINAL_ITEM_STATUSES:
                journal.failed(run.run_id, row.profile, row.file, reason)
        journal.finish_run(run.run_id, generator_runs.FAILED, reason)
    except JournalError as ex:
        logger.bind(run_id=run.run_id, error=str(ex)).critical(
            "Не удалось закрыть ран генератора после сбоя"
        )


def _active_checker(
    settings: GeneratorSettings,
    injected: safety._Checker | None,
) -> safety._Checker | None:
    """Return the safety checker, running the Clef preflight before any rent."""
    if not settings.safety_enabled:
        return None
    checker = injected or safety.FrameChecker(settings)
    checker.preflight()
    return checker


def _write(report: RunReport, run_dir: Path) -> None:
    """Write ``run.json`` atomically into the run directory."""
    run_dir.mkdir(parents=True, exist_ok=True)
    target = run_dir / "run.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    temporary.replace(target)


def _started(run: generator_runs.GeneratorRunRow) -> datetime:
    """Parse the start moment of a run, falling back to now."""
    try:
        moment = datetime.fromisoformat(run.started_at)
    except ValueError:
        return datetime.now(UTC)
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _text(value: object) -> str | None:
    """Return an optional non-empty string."""
    if value is None:
        return None
    text = str(value)
    return text or None


def _integer(value: object) -> int | None:
    """Return an optional integer."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _now() -> str:
    """Return the current UTC moment as ISO 8601."""
    return datetime.now(UTC).isoformat()


__all__ = ["RunOutcome", "resume_unfinished"]
