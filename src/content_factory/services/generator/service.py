"""Dating batch pipeline: plan, rent, generate, post-process and report."""

from __future__ import annotations

import os
import random
import shutil
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from loguru import logger

from content_factory.config.settings import (
    GeneratorSettings,
    get_settings,
    load_generator_settings,
)
from content_factory.constants import CONTENT_DIR, PROFILES_DIR
from content_factory.exceptions.generator import GeneratorError, ProfileError
from content_factory.schemas.generator.run import (
    RunItem,
    RunReport,
    RunServer,
    RunStatus,
)
from content_factory.schemas.generator.server import ServerConnection, ServerInfo
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
from content_factory.services.generator.plan import (
    _PlannedItem,
    attempt_seed,
    plan_batch,
    validate_count,
)
from content_factory.services.generator.profiles import _Profile, load_profile
from content_factory.services.generator.report import (
    _FINAL_DIR,
    _finalize,
    _report,
    _status,
    _write_report,
)
from content_factory.services.generator.vast import VastSession
from content_factory.services.generator.workflow import build_workflow

_SOURCE_DIR = "source"
_TEXT_DIR = "text"


class _ServerSession(Protocol):
    """Rented server used by one profile batch."""

    def open(self) -> ServerInfo:
        """Rent the server and return its ready connection."""
        ...

    def destroy(self) -> None:
        """Destroy the server and verify it is gone."""
        ...


_SessionFactory = Callable[[_Profile, GeneratorSettings], _ServerSession]
_ClientFactory = Callable[[ServerConnection], comfy._ComfyTransport]
_Store = Callable[[Path, bytes], None]


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
    data_dir: Path | None = None,
    settings: GeneratorSettings | None = None,
    session_factory: _SessionFactory | None = None,
    client_factory: _ClientFactory | None = None,
    text_writer: texts._TextWriter | None = None,
    store: _Store | None = None,
    safety_checker: safety._Checker | None = None,
    rng: random.Random | None = None,
    now: datetime | None = None,
) -> _BatchResult:
    """Produce a batch of dating videos and write ``run.json``.

    Every profile is validated and every plan is built before the first
    server is rented. The server is destroyed as soon as generation ends,
    before the text, overlay and music steps, and the teardown is verified
    against the Vast.ai instance list. With safety enabled, one Clef request
    on a synthetic frame runs before the rent; every downloaded source is
    then checked frame by frame before the text card is drawn.

    Args:
        profiles: Comma-separated profile names, for example
            ``"dating/profile_2,dating/profile_1"``.
        count: Videos per profile, at least one.
        dry_run: Build plans and write ``run.json`` without any network call.
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
        GeneratorError: ``count`` is not positive or the Clef preflight
            fails before any server is rented.
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
    names = _parse_names(profiles)
    validate_count(count)
    randomizer = rng or random.Random()  # noqa: S311 — variety, not secrets
    loaded = [load_profile(name, root) for name in names]
    started = now or datetime.now(UTC)
    run_id = _choose_run_id(config.output_dir, loaded, started)
    prepared = _prepare(config, loaded, count, run_id, randomizer)
    checker = _prepare_media(config, dry_run, safety_checker)
    data_root = root.parent
    run_dirs, generated, failed = _run_all(
        prepared,
        config,
        run_id,
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
    )
    return _BatchResult(
        run_id=run_id,
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
    config: GeneratorSettings,
    run_id: str,
    started: datetime,
    count: int,
    dry_run: bool,
    session_factory: _SessionFactory | None,
    client_factory: _ClientFactory | None,
    text_writer: texts._TextWriter | None,
    store: _Store | None,
    checker: safety._Checker | None,
    randomizer: random.Random,
    data_root: Path,
) -> tuple[list[Path], int, int]:
    """Write one report per profile, produce its videos and count the outcomes."""
    run_dirs: list[Path] = []
    generated = 0
    failed = 0
    for profile, items in prepared:
        run_dir = config.output_dir / profile.language / _slug(profile) / run_id
        report = _report(profile, items, run_id, started, count, dry_run)
        _write_report(run_dir, report)
        run_dirs.append(run_dir)
        if dry_run:
            continue
        _produce_safely(
            profile,
            items,
            run_dir,
            report,
            config,
            session_factory or _default_session,
            client_factory or comfy.ComfyClient,
            text_writer,
            store,
            checker,
            randomizer,
            data_root,
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
) -> list[tuple[_Profile, tuple[_PlannedItem, ...]]]:
    """Read the rotation cursor of every profile and build its batch plan."""
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


def _produce_safely(
    profile: _Profile,
    items: tuple[_PlannedItem, ...],
    run_dir: Path,
    report: RunReport,
    settings: GeneratorSettings,
    session_factory: _SessionFactory,
    client_factory: _ClientFactory,
    text_writer: texts._TextWriter | None,
    store: _Store | None,
    checker: safety._Checker | None,
    rng: random.Random,
    data_root: Path,
) -> None:
    """Run one profile and mark its report failed before re-raising a fatal error."""
    try:
        _produce(
            profile,
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
        )
    except BaseException:
        report.status = RunStatus.FAILED
        report.finished_at = datetime.now(UTC)
        _write_report(run_dir, report)
        raise


def _produce(
    profile: _Profile,
    items: tuple[_PlannedItem, ...],
    run_dir: Path,
    report: RunReport,
    settings: GeneratorSettings,
    session_factory: _SessionFactory,
    client_factory: _ClientFactory,
    text_writer: texts._TextWriter | None,
    store: _Store | None,
    checker: safety._Checker | None,
    rng: random.Random,
    data_root: Path,
) -> None:
    """Run the remote phase and then the local post-processing."""
    session = session_factory(profile, settings)
    try:
        info = session.open()
        report.server = _server(profile, info)
        report.status = RunStatus.GENERATING
        _write_report(run_dir, report)
        client = client_factory(info.connection)
        try:
            results = comfy.run_jobs(
                client,
                items,
                lambda item, attempt: build_workflow(
                    profile,
                    item,
                    profiles.build_prompt(profile, item.look, rng),
                    seed=attempt_seed(item.seed, attempt),
                ),
                run_dir / _SOURCE_DIR,
                store=store,
                verify=checker.verify if checker is not None else None,
                safety_frame_attempts=settings.safety_frame_attempts,
            )
        finally:
            client.close()
    finally:
        _destroy(session, profile.name)
    report.status = RunStatus.POSTPROCESSING
    _write_report(run_dir, report)
    _postprocess(profile, results, run_dir, report, settings, text_writer, rng, data_root)
    _finalize(report, run_dir, items)


def _postprocess(
    profile: _Profile,
    results: tuple[comfy._JobResult, ...],
    run_dir: Path,
    report: RunReport,
    settings: GeneratorSettings,
    text_writer: texts._TextWriter | None,
    rng: random.Random,
    data_root: Path,
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
            _write_report(run_dir, report)
            continue
        try:
            _finish_item(profile, item, result.path, run_dir, writer, rng, data_root)
        except (GeneratorError, OSError) as ex:
            item.status = RunStatus.FAILED
            item.error = str(ex)
            logger.bind(file=item.file, error=str(ex), error_type=type(ex).__name__).error(
                "Постобработка ролика не удалась"
            )
        else:
            _advance_cursor(settings.output_dir, profile, item, report.run_id)
            logger.bind(file=item.file, duration=item.duration).info("Ролик готов")
        _write_report(run_dir, report)
    for item in report.items:
        if item.status not in (RunStatus.DONE, RunStatus.FAILED):
            item.status = RunStatus.FAILED
            item.error = item.error or "результат не получен"
    _write_report(run_dir, report)


def _advance_cursor(output_dir: Path, profile: _Profile, item: RunItem, run_id: str) -> None:
    """Move the rotation cursor after a finished video without failing it."""
    try:
        rotation.advance_cursor(
            _cursor_path(output_dir, profile),
            item.look_id,
            run_id,
            datetime.now(UTC),
        )
    except OSError as ex:
        logger.bind(
            profile=profile.name,
            look_id=item.look_id,
            error=str(ex),
            error_type=type(ex).__name__,
        ).warning("Курсор карточек не обновлён — следующий прогон может повторить карточку")


def _finish_item(
    profile: _Profile,
    item: RunItem,
    source: Path,
    run_dir: Path,
    writer: texts._TextWriter,
    rng: random.Random,
    data_root: Path,
) -> None:
    """Run the local stages of one video and publish its final into the stock."""
    card = texts.generate_overlay(profile, writer, rng=rng)
    texts.save_overlay(run_dir / _TEXT_DIR / f"{Path(item.file).stem}.json", card)
    audio = rng.choice(profile.audio_tracks) if profile.audio_tracks else None
    final = run_dir / _FINAL_DIR / item.file
    overlay.mix(profile, card, source, final, audio)
    info = qa.check_video(
        final,
        media.video_seconds(item.frames, profile.fps),
        expect_audio=bool(profile.audio_tracks),
    )
    publish_stock(profile, final, data_root)
    item.duration = round(info.duration, 2)
    item.status = RunStatus.DONE


def publish_stock(profile: _Profile, final: Path, data_root: Path) -> Path:
    """Publish one final video into ``content/<niche>/<language>/``.

    A hard link keeps the video in ``generated/`` without spending disk space.
    When linking is impossible, the file is copied instead. An existing stock
    file is never overwritten: that is a publication error.

    Args:
        profile: Profile that produced the video; an empty niche falls back
            to the profile category.
        final: Finished mp4 inside the run ``final`` directory.
        data_root: Root ``app_data`` directory.

    Returns:
        Path of the published stock file.

    Raises:
        GeneratorError: The stock file already exists, the destination cannot
            be created or neither linking nor copying succeeded.
    """
    niche = profile.niche or profile.name.split("/", 1)[0]
    destination = data_root / CONTENT_DIR / niche / profile.language / final.name
    if destination.exists():
        raise GeneratorError(_publish_error(destination))
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as ex:
        raise GeneratorError(_publish_error(destination)) from ex
    try:
        os.link(final, destination)
    except FileExistsError as ex:
        raise GeneratorError(_publish_error(destination)) from ex
    except OSError:
        try:
            shutil.copy2(final, destination)
        except OSError as ex:
            raise GeneratorError(_publish_error(destination)) from ex
    return destination


def _publish_error(destination: Path) -> str:
    """Return the user-facing text of a failed stock publication."""
    return f"не удалось положить в сток: {destination}"


def _destroy(session: _ServerSession, profile: str) -> None:
    """Destroy the server without masking an already active exception."""
    active = sys.exc_info()[0] is not None
    try:
        session.destroy()
    except Exception as ex:  # noqa: BLE001 — teardown must not hide the original error
        logger.bind(profile=profile, error=str(ex), error_type=type(ex).__name__).critical(
            "Сервер не уничтожен — погасите вручную"
        )
        if not active:
            raise


def _default_session(profile: _Profile, settings: GeneratorSettings) -> _ServerSession:
    """Build the real Vast.ai session of one profile."""
    return VastSession(profile, settings)


def _choose_run_id(output_dir: Path, loaded: list[_Profile], moment: datetime) -> str:
    """Return a UTC run id whose directories do not exist yet."""
    stamp = moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    for suffix in range(1, 100):
        candidate = stamp if suffix == 1 else f"{stamp}-{suffix}"
        if not any(
            (output_dir / profile.language / _slug(profile) / candidate).exists()
            for profile in loaded
        ):
            return candidate
    raise GeneratorError(f"не удалось подобрать свободный run_id для {stamp}")


def _parse_names(value: str) -> list[str]:
    """Split the flow parameter into unique profile names."""
    names: list[str] = []
    for part in str(value or "").split(","):
        name = part.strip()
        if name and name not in names:
            names.append(name)
    if not names:
        raise ProfileError("не указан ни один профиль")
    return names


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


def _slug(profile: _Profile) -> str:
    """Return the profile directory name used in output paths."""
    return profile.name.replace("/", "_")


def _cursor_path(output_dir: Path, profile: _Profile) -> Path:
    """Return the rotation cursor path of one ``language + profile`` pair."""
    return output_dir / profile.language / _slug(profile) / rotation.ROTATION_FILENAME


__all__ = ["generate_batch", "publish_stock"]
