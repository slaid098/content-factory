"""Dating batch pipeline: plan, rent, generate, post-process and report."""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from loguru import logger

from content_factory.config.settings import (
    GeneratorSettings,
    get_settings,
    load_generator_settings,
)
from content_factory.constants import PROFILES_DIR
from content_factory.exceptions.generator import GeneratorError
from content_factory.schemas.generator.journal import RunJournal
from content_factory.schemas.generator.run import RunStatus
from content_factory.schemas.generator.text import TextWriter
from content_factory.services.generator import media, profiles, rotation, safety
from content_factory.services.generator.batch import _run_all
from content_factory.services.generator.comfy import (
    _ClientFactory,
    _SessionFactory,
    _Store,
)
from content_factory.services.generator.plan import (
    _PlannedItem,
    plan_batch,
    planned_item,
    validate_count,
)
from content_factory.services.generator.profiles import (
    _Profile,
    choose_run_id,
    load_profile,
    parse_names,
    vast_layer,
)
from content_factory.services.generator.report import _status
from content_factory.services.generator.resume import resume_unfinished
from content_factory.services.generator.rotation import read_profile_cursor
from content_factory.utils.run_id import validate_run_id

PlannedItem = _PlannedItem


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
        journal: Run journal; the calling flow passes its writer.
        plan: Plan restored from the journal; never rebuilt when present.

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
    return _batch_result(selected_run_id, dry_run, run_dirs, generated, failed)


def _batch_result(
    run_id: str,
    dry_run: bool,
    run_dirs: Sequence[Path],
    generated: int,
    failed: int,
) -> _BatchResult:
    """Build the outcome of one batch from its counters."""
    status = RunStatus.DRY_RUN.value if dry_run else _status(generated, failed)
    return _BatchResult(
        run_id=run_id,
        status=status,
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
                start_after=rotation.read_cursor(
                    rotation.cursor_path(
                        config.output_dir,
                        profile.language,
                        profiles.profile_slug(profile),
                    )
                ),
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


__all__ = [
    "PlannedItem",
    "generate_batch",
    "planned_item",
    "read_profile_cursor",
    "resume_unfinished",
]
