"""Dating batch pipeline: plan, rent, generate, post-process and report."""

from __future__ import annotations

import random
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from content_factory.config.settings import (
    GeneratorSettings,
    get_settings,
    load_generator_settings,
)
from content_factory.constants import PROFILES_DIR
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
from content_factory.services.generator.plan import (
    _PlannedItem,
    attempt_seed,
    plan_batch,
    validate_count,
)
from content_factory.services.generator.profiles import (
    _Profile,
    choose_run_id,
    load_profile,
    parse_names,
    vast_layer,
)
from content_factory.services.generator.publish import publish_stock
from content_factory.services.generator.report import (
    _FINAL_DIR,
    _finalize,
    _report,
    _status,
    _write_report,
)
from content_factory.services.generator.rotation import read_profile_cursor
from content_factory.services.generator.session_factory import _production_sessions
from content_factory.services.generator.workflow import build_workflow
from content_factory.utils.run_id import profile_run_dir, validate_run_id

_SOURCE_DIR = "source"
_TEXT_DIR = "text"


@dataclass(frozen=True, slots=True)
class _BatchResult:
    """Outcome of one ``generate_batch`` call."""

    run_id: str
    status: str
    run_dirs: tuple[Path, ...]
    generated: int
    failed: int
    dry_run: bool


def generate_batch(
    profiles: str = "dating/profile_2",
    count: int = 1,
    dry_run: bool = False,
    *,
    run_id: str | None = None,
    data_dir: Path | None = None,
    settings: GeneratorSettings | None = None,
    session_factory: _SessionFactory | None = None,
    client_factory: _ClientFactory | None = None,
    text_writer: TextWriter | None = None,
    store: _Store | None = None,
    safety_checker: safety._Checker | None = None,
    rng: random.Random | None = None,
    now: datetime | None = None,
    journal: RunJournal | None = None,
    plan: Sequence[_PlannedItem] | None = None,
) -> _BatchResult:
    """Produce a batch of dating videos and write ``run.json``.

    Every profile and plan is validated before the first rent. The server is
    destroyed as soon as generation ends, before the text, overlay and music
    steps, and the teardown is verified against the Vast.ai instance list.
    With safety enabled, one Clef request on a synthetic frame runs before the
    rent; every downloaded source is then checked before the text card.

    Args:
        profiles: Comma-separated profile names, for example
            ``"dating/profile_2,dating/profile_1"``.
        count: Videos per profile, at least one.
        dry_run: Build plans and write ``run.json`` without any network call.
        run_id: Run id chosen by the calling pipeline; picked locally when
            omitted.
        data_dir: Runtime data directory; defaults to the process settings.
        settings: Generator settings; defaults to the environment.
        session_factory: Rental factory; tests inject a fake server.
        client_factory: ComfyUI client factory; tests inject a fake client.
        text_writer: Text model port; tests inject a fake writer.
        store: Saver of downloaded bytes; tests avoid calling ffmpeg.
        safety_checker: Clef frame checker; tests inject a fake one.
        rng: Random source, seeded in tests.
        now: Run start moment, fixed in tests.

    Returns:
        Run id, final status, output directories and counters.

    Raises:
        GeneratorError: ``count`` is not positive or the Clef preflight fails
            before rent.
        SafetyError: Clef cannot check the synthetic preflight frame.
        ProfileError: A profile is missing or malformed, or a batch asks for
            repeats while the profile has only one card.
    """
    config = settings or load_generator_settings()
    if not config.safety_enabled:
        logger.bind(stage="safety").warning(
            "Проверка кадров Clef выключена: GENERATOR_SAFETY_ENABLED=false"
        )
    root = (data_dir or get_settings().data_dir) / PROFILES_DIR
    names = parse_names(profiles)
    validate_count(count)
    randomizer = rng or random.Random()  # noqa: S311 — variety, not secrets
    loaded = [load_profile(name, root) for name in names]
    started = now or datetime.now(UTC)
    selected_run_id = validate_run_id(
        run_id if run_id is not None else choose_run_id(config.output_dir, loaded, started)
    )
    layers = [vast_layer(profile) for profile in loaded]
    prepared = _prepare(config, loaded, count, selected_run_id, randomizer, plan)
    checker = _prepare_media(config, dry_run, safety_checker)
    data_root = root.parent
    run_dirs, generated, failed = _run_all(
        prepared,
        layers,
        config,
        selected_run_id,
        started,
        count,
        dry_run,
        session_factory,
        client_factory,
        text_writer,
        store,
        checker,
        randomizer,
        data_root,
        journal,
    )
    return _BatchResult(
        run_id=selected_run_id,
        status=RunStatus.DRY_RUN.value if dry_run else _status(generated, failed),
        run_dirs=tuple(run_dirs),
        generated=generated,
        failed=failed,
        dry_run=dry_run,
    )


def _prepare_media(
    config: GeneratorSettings,
    dry_run: bool,
    injected: safety._Checker | None,
) -> safety._Checker | None:
    """Check free space and run the Clef preflight before the first rent."""
    if dry_run:
        return None
    media.ensure_free_space(config.output_dir)
    if not config.safety_enabled:
        return None
    checker = injected or safety.FrameChecker(config)
    checker.preflight()
    return checker


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


def _prepare(
    config: GeneratorSettings,
    loaded: list[_Profile],
    count: int,
    run_id: str,
    randomizer: random.Random,
    plan: Sequence[_PlannedItem] | None = None,
) -> list[tuple[_Profile, tuple[_PlannedItem, ...]]]:
    """Read the rotation cursor of every profile and build its batch plan.

    An explicit *plan* comes from the run journal and is never rebuilt: the
    producers keep the cards, seeds and lengths written before the first
    network call.
    """
    if plan is not None:
        return _prepare_from_plan(loaded, count, plan)
    return [
        (
            profile,
            plan_batch(
                profile,
                count,
                run_id,
                rng=randomizer,
                start_after=rotation.read_cursor(_cursor_path(config.output_dir, profile)),
            ),
        )
        for profile in loaded
    ]


def _prepare_from_plan(
    loaded: list[_Profile],
    count: int,
    plan: Sequence[_PlannedItem],
) -> list[tuple[_Profile, tuple[_PlannedItem, ...]]]:
    """Group journal items by profile and require exactly *count* per profile."""
    prepared: list[tuple[_Profile, tuple[_PlannedItem, ...]]] = []
    for profile in loaded:
        items = tuple(item for item in plan if item.profile == profile.name)
        if len(items) != count:
            raise GeneratorError(
                f"журнал рана дал {len(items)} элементов профиля {profile.name!r}, "
                f"ожидалось {count}"
            )
        prepared.append((profile, items))
    unknown = {item.profile for item in plan} - {profile.name for profile in loaded}
    if unknown:
        raise GeneratorError(f"журнал рана содержит чужие профили: {sorted(unknown)}")
    return prepared


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
    prompt_lock = threading.Lock()

    def build(item: _PlannedItem, attempt: int) -> dict[str, Any]:
        with prompt_lock:
            prompt = profiles.build_prompt(profile, item.look, rng)
        return build_workflow(profile, item, prompt, seed=attempt_seed(item.seed, attempt))

    def verify(item: _PlannedItem, source: Path, attempt: int) -> str | None:
        reason = checker.verify(item, source, attempt) if checker is not None else None
        if reason:
            return reason
        extra = tuple(name for name in layer.checks if name != "clef")
        return production.run_qa(extra, production.QA_SOURCE, {}) if extra else None

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

    report.status = RunStatus.GENERATING
    _write_report(run_dir, report)
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
        report.server = _server(profile, batch.servers[0]) if batch.servers else None
        results = batch.results
    else:
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
        report.server = _server(profile, info)
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
    path = _cursor_path(output_dir, profile)
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


def _cursor_path(output_dir: Path, profile: _Profile) -> Path:
    """Return the rotation cursor path of one ``language + profile`` pair."""
    return (
        output_dir / profile.language / profiles.profile_slug(profile) / rotation.ROTATION_FILENAME
    )


__all__ = ["generate_batch", "publish_stock", "read_profile_cursor"]
