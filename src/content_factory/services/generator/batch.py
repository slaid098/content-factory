"""Remote generation and local post-processing of one batch."""

from __future__ import annotations

import random
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from content_factory.config.settings import GeneratorSettings
from content_factory.exceptions.generator import GeneratorError
from content_factory.schemas.generator.journal import RunJournal
from content_factory.schemas.generator.pipeline import Layer, content_type
from content_factory.schemas.generator.run import (
    RunItem,
    RunReport,
    RunServer,
    RunStatus,
    RunStep,
)
from content_factory.schemas.generator.server import ServerInfo
from content_factory.schemas.generator.text import TextWriter
from content_factory.services import production
from content_factory.services.generator import (
    comfy,
    media,
    overlay,
    profiles,
    qa,
    rotation,
    safety,
    texts,
)
from content_factory.services.generator.comfy import (
    _ClientFactory,
    _SessionFactory,
    _Store,
)
from content_factory.services.generator.plan import _PlannedItem
from content_factory.services.generator.profiles import _Profile
from content_factory.services.generator.publish import publish_stock
from content_factory.services.generator.report import (
    _FINAL_DIR,
    _finalize,
    _report,
    _write_report,
)
from content_factory.services.generator.session_factory import _production_sessions
from content_factory.services.generator.workflow import workflow_builder
from content_factory.utils.run_id import profile_run_dir

_SOURCE_DIR = "source"
_TEXT_DIR = "text"


def _run_all(
    prepared: list[tuple[_Profile, tuple[_PlannedItem, ...]]],
    layers: list[Layer],
    config: GeneratorSettings,
    run_id: str,
    started: datetime,
    count: int,
    dry_run: bool,
    session_factory: _SessionFactory | None,
    client_factory: _ClientFactory | None,
    text_writer: TextWriter | None,
    store: _Store | None,
    checker: safety._Checker | None,
    randomizer: random.Random,
    data_root: Path,
    journal: RunJournal | None = None,
) -> tuple[list[Path], int, int]:
    """Write one report per profile, produce its videos and count the outcomes."""
    run_dirs: list[Path] = []
    generated = 0
    failed = 0
    for (profile, items), layer in zip(prepared, layers, strict=True):
        slug = profiles.profile_slug(profile)
        run_dir = profile_run_dir(config.output_dir, profile.language, slug, run_id)
        report = _report(profile, items, run_id, started, count, dry_run)
        report.production = [
            RunStep(
                producer=entry.producer,
                type=content_type(entry.output),
                qa=list(entry.checks),
            )
            for entry in profile.production
        ]
        for item in report.items:
            item.producer = layer.producer
            item.type = content_type(layer.output)
            item.qa = list(layer.checks)
        _write_report(run_dir, report)
        run_dirs.append(run_dir)
        if dry_run:
            continue
        _produce_safely(
            profile,
            layer,
            items,
            run_dir,
            report,
            config,
            session_factory or _production_sessions(data_root, run_id),
            client_factory or comfy.ComfyClient,
            text_writer,
            store,
            checker,
            randomizer,
            data_root,
            journal,
        )
        generated += report.totals.generated
        failed += report.totals.failed
    return run_dirs, generated, failed


def _produce_safely(
    profile: _Profile,
    layer: Layer,
    items: tuple[_PlannedItem, ...],
    run_dir: Path,
    report: RunReport,
    settings: GeneratorSettings,
    session_factory: _SessionFactory,
    client_factory: _ClientFactory,
    text_writer: TextWriter | None,
    store: _Store | None,
    checker: safety._Checker | None,
    rng: random.Random,
    data_root: Path,
    journal: RunJournal | None = None,
) -> None:
    """Run one profile and fail its remaining items before re-raising an error."""
    try:
        _produce(
            profile,
            layer,
            items,
            run_dir,
            report,
            settings,
            session_factory,
            client_factory,
            text_writer,
            store,
            checker,
            rng,
            data_root,
            journal,
        )
    except BaseException as ex:
        report.status = RunStatus.FAILED
        report.finished_at = datetime.now(UTC)
        for item in report.items:
            if item.status not in (RunStatus.DONE, RunStatus.FAILED):
                item.status, item.error = RunStatus.FAILED, str(ex)
                if journal is not None:
                    journal.failed(report.run_id, item.profile, item.file, str(ex))
        _write_report(run_dir, report)
        raise


def _produce(
    profile: _Profile,
    layer: Layer,
    items: tuple[_PlannedItem, ...],
    run_dir: Path,
    report: RunReport,
    settings: GeneratorSettings,
    session_factory: _SessionFactory,
    client_factory: _ClientFactory,
    text_writer: TextWriter | None,
    store: _Store | None,
    checker: safety._Checker | None,
    rng: random.Random,
    data_root: Path,
    journal: RunJournal | None = None,
) -> None:
    """Run the remote phase and then the local post-processing."""
    report.status = RunStatus.GENERATING
    _write_report(run_dir, report)
    results, server = _remote(
        profile,
        layer,
        items,
        run_dir,
        report,
        settings,
        session_factory,
        client_factory,
        store,
        checker,
        rng,
        journal,
    )
    report.server = server
    report.status = RunStatus.POSTPROCESSING
    _write_report(run_dir, report)
    _postprocess(
        profile,
        layer,
        results,
        run_dir,
        report,
        settings,
        text_writer,
        rng,
        data_root,
        journal,
    )
    _finalize(report, run_dir, items)


def _remote(
    profile: _Profile,
    layer: Layer,
    items: tuple[_PlannedItem, ...],
    run_dir: Path,
    report: RunReport,
    settings: GeneratorSettings,
    session_factory: _SessionFactory,
    client_factory: _ClientFactory,
    store: _Store | None,
    checker: safety._Checker | None,
    rng: random.Random,
    journal: RunJournal | None,
) -> tuple[tuple[comfy._JobResult, ...], RunServer | None]:
    """Generate the sources of one profile and return its server block."""
    build = workflow_builder(profile, rng)
    verify = _remote_verifier(layer, checker)

    def on_submit(item: _PlannedItem, attempt: int, prompt_id: str, contract_id: str) -> None:
        if journal is not None:
            journal.submitted(
                report.run_id,
                profile.name,
                item.filename,
                prompt_id,
                attempt,
                contract_id,
            )

    def on_download(item: _PlannedItem, source: Path) -> None:
        if journal is not None:
            journal.downloaded(report.run_id, profile.name, item.filename, str(source))

    if comfy.server_count(len(items), settings) > 1:
        batch = comfy.run_servers(
            profile,
            settings,
            comfy.split_batch(items, settings),
            session_factory,
            client_factory,
            build,
            run_dir / _SOURCE_DIR,
            store=store,
            verify=verify,
            on_submit=on_submit,
            on_download=on_download,
        )
        server = _server(profile, batch.servers[0]) if batch.servers else None
        return batch.results, server
    info, results = comfy.run_server(
        session_factory(profile, settings),
        client_factory,
        items,
        build,
        run_dir / _SOURCE_DIR,
        label=profile.name,
        settings=settings,
        store=store,
        verify=verify,
        on_submit=on_submit,
        on_download=on_download,
    )
    return results, _server(profile, info)


def _remote_verifier(
    layer: Layer,
    checker: safety._Checker | None,
) -> Callable[[_PlannedItem, Path, int], str | None]:
    """Build the source check of the remote phase."""

    def verify(item: _PlannedItem, source: Path, attempt: int) -> str | None:
        reason = checker.verify(item, source, attempt) if checker is not None else None
        if reason:
            return reason
        extra = tuple(name for name in layer.checks if name != "clef")
        return production.run_qa(extra, production.QA_SOURCE, {}) if extra else None

    return verify


def _postprocess(
    profile: _Profile,
    layer: Layer,
    results: tuple[comfy._JobResult, ...],
    run_dir: Path,
    report: RunReport,
    settings: GeneratorSettings,
    text_writer: TextWriter | None,
    rng: random.Random,
    data_root: Path,
    journal: RunJournal | None = None,
) -> None:
    """Generate texts, overlay cards, mix music and check every mp4."""
    writer = text_writer or texts.TextClient(settings)
    by_file = {entry.file: entry for entry in report.items}
    for result in results:
        item = by_file.get(result.item.filename)
        if item is None:
            continue
        if result.path is None:
            item.status = RunStatus.FAILED
            item.error = result.error or "источник не сгенерирован"
            _fail_item(journal, report.run_id, item, item.error)
            _write_report(run_dir, report)
            continue
        try:
            _finish_item(
                profile,
                layer,
                item,
                result.path,
                run_dir,
                writer,
                rng,
                data_root,
                journal,
                report.run_id,
            )
        except (GeneratorError, OSError) as ex:
            item.status = RunStatus.FAILED
            item.error = str(ex)
            _fail_item(journal, report.run_id, item, item.error)
            logger.bind(file=item.file, error=str(ex), error_type=type(ex).__name__).error(
                "Постобработка ролика не удалась"
            )
        else:
            _advance_cursor(settings.output_dir, profile, item, report.run_id)
            logger.bind(file=item.file, duration=item.duration).info("Ролик готов")
        _write_report(run_dir, report)
    for item in report.items:
        if item.status not in (RunStatus.DONE, RunStatus.FAILED):
            item.status, item.error = RunStatus.FAILED, item.error or "результат не получен"
            _fail_item(journal, report.run_id, item, item.error)
    _write_report(run_dir, report)


def _fail_item(
    journal: RunJournal | None,
    run_id: str,
    item: RunItem,
    error: str,
) -> None:
    """Mark one journal item failed when a run carries a journal."""
    if journal is not None:
        journal.failed(run_id, item.profile, item.file, error)


def _advance_cursor(output_dir: Path, profile: _Profile, item: RunItem, run_id: str) -> None:
    """Move the rotation cursor forward after a finished video.

    A cursor written by the same run is never moved back: when several chunks
    finish out of order, the later card keeps the cursor. A cursor left by an
    earlier run may move to any card, so a batch that wraps around the card
    list still advances.
    """
    if item.look_id is None:
        return
    path = rotation.cursor_path(output_dir, profile.language, profiles.profile_slug(profile))
    state = rotation.read_cursor_state(path)
    if state is not None:
        current_id, current_run = state
        order = [look.id for look in profile.looks]
        if (
            current_run == run_id
            and current_id in order
            and item.look_id in order
            and order.index(item.look_id) <= order.index(current_id)
        ):
            return
    try:
        rotation.advance_cursor(path, item.look_id, run_id, datetime.now(UTC))
    except OSError as ex:
        logger.bind(
            profile=profile.name,
            look_id=item.look_id,
            error=str(ex),
            error_type=type(ex).__name__,
        ).warning("Курсор карточек не обновлён — следующий прогон может повторить карточку")


def _finish_item(
    profile: _Profile,
    layer: Layer,
    item: RunItem,
    source: Path,
    run_dir: Path,
    writer: TextWriter,
    rng: random.Random,
    data_root: Path,
    journal: RunJournal | None = None,
    run_id: str | None = None,
) -> None:
    """Run the local stages of one video and publish its final into the stock."""
    if item.frames is None:
        raise GeneratorError(f"у ролика {item.file} не задано число кадров")
    card = _load_or_generate_card(profile, item, run_dir, writer, rng, journal is not None)
    audio = _choose_audio(profile, item, run_dir, rng, journal is not None)
    final = run_dir / _FINAL_DIR / item.file
    overlay.mix(profile, card, source, final, audio)
    info = qa.check_declared(
        layer.checks,
        final,
        media.video_seconds(item.frames, profile.fps),
        expect_audio=bool(profile.audio_tracks),
    )
    if journal is not None and run_id is not None:
        journal.postprocessing(run_id, item.profile, item.file, str(final))
    publish_stock(profile, final, data_root)
    if journal is not None and run_id is not None:
        journal.published(run_id, item.profile, item.file)
    item.duration = round(info.duration, 2)
    item.status = RunStatus.DONE


def _load_or_generate_card(
    profile: _Profile,
    item: RunItem,
    run_dir: Path,
    writer: TextWriter,
    rng: random.Random,
    reuse: bool,
) -> texts._Overlay:
    """Reuse the saved card of a resumed item or generate and save a new one."""
    path = run_dir / _TEXT_DIR / f"{Path(item.file).stem}.json"
    if reuse:
        saved = texts.load_overlay(path)
        if saved is not None:
            return saved
    card = texts.generate_overlay(profile, writer, rng=rng)
    texts.save_overlay(path, card)
    return card


def _choose_audio(
    profile: _Profile,
    item: RunItem,
    run_dir: Path,
    rng: random.Random,
    reuse: bool,
) -> Path | None:
    """Reuse the saved music choice of a resumed item or pick a new one."""
    if not profile.audio_tracks:
        return None
    path = run_dir / _TEXT_DIR / f"{Path(item.file).stem}.audio"
    if reuse and path.is_file():
        saved = path.read_text(encoding="utf-8").strip()
        if not saved:
            return None
        candidate = Path(saved)
        if candidate.is_file():
            return candidate
    audio = rng.choice(profile.audio_tracks)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{audio}\n", encoding="utf-8")
    return audio


def _server(profile: _Profile, info: ServerInfo) -> RunServer:
    """Build the ``server`` block of ``run.json``."""
    connection = info.connection
    return RunServer(
        contract_id=connection.contract_id,
        ip=connection.ip,
        gpu=connection.gpu or profile.gpu,
        image=profile.image,
        cost_per_hour=info.cost_per_hour,
    )
