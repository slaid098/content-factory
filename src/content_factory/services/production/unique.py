"""Producer of unique profile videos from the imported source pool.

The producer takes the next sources of the profile cursor, draws the usual
text card and profile music over each source, runs the declared QA checks and
publishes the final mp4 into the profile stock. No Vast.ai server is rented
and ComfyUI is not called; the text model and the declared Clef check are the
only network calls.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger

from content_factory.config.settings import GeneratorSettings
from content_factory.constants import PROFILES_DIR
from content_factory.exceptions.generator import GeneratorError, QaError
from content_factory.schemas.content.delivery import ContentType
from content_factory.schemas.generator.run import (
    ProducedBatch,
    ProductionStep,
    ProfileRef,
    RunItem,
    RunReport,
    RunStatus,
    RunStep,
    TextWriter,
)
from content_factory.schemas.generator.source import SourceItem
from content_factory.services.generator import (
    FrameChecker,
    TextClient,
    check_declared,
    choose_run_id,
    generate_overlay,
    load_profile,
    mix,
    profile_slug,
    publish_stock,
    save_overlay,
    validate_count,
    write_report,
)
from content_factory.services.production.registry import UNIQUE_PRODUCER
from content_factory.services.production.reporting import close_report, find_step, mark_failed
from content_factory.services.sources import mark_published, plan_next

_FINAL_DIR = "final"
_TEXT_DIR = "text"


@dataclass(frozen=True, slots=True)
class _LogItem:
    """File name passed to the safety checker as log context."""

    filename: str


@dataclass(frozen=True, slots=True)
class _Seams:
    """Optional test seams of the producer, one value for the run."""

    writer: TextWriter | None
    checker: FrameChecker | None
    rng: random.Random | None
    now: datetime | None


class UniqueProducer:
    """Build videos by overlaying profile content on imported sources."""

    supported_types = frozenset({ContentType.VIDEO})

    def __init__(
        self,
        *,
        writer: TextWriter | None = None,
        checker: FrameChecker | None = None,
        rng: random.Random | None = None,
        now: datetime | None = None,
    ) -> None:
        """Store optional test seams; production uses the real ports.

        Args:
            writer: Text model port; defaults to :class:`TextClient`.
            checker: Clef frame checker; tests inject a fake one.
            rng: Random source for the card and music choice.
            now: Run start moment, fixed in tests.
        """
        self._seams = _Seams(writer, checker, rng, now)

    def produce(
        self,
        profile: ProfileRef,
        count: int,
        data_dir: Path,
        settings: GeneratorSettings,
    ) -> ProducedBatch:
        """Produce *count* videos from the next sources of one profile.

        Args:
            profile: Profile whose recipe, card and music are used.
            count: Number of videos, at least one.
            data_dir: Root ``app_data`` directory with sources, profiles and
                the operational database.
            settings: Generator settings with the output directory.

        Returns:
            Run id, counters and the run directory of the step.

        Raises:
            GeneratorError: ``count`` is invalid, the profile has no
                ``unique/video`` step, or the pool has fewer than *count*
                available sources. Nothing is rendered or published then.
            DatabaseError: The pool rows cannot be read or updated.
        """
        validate_count(count)
        loaded = load_profile(profile.name, data_dir / PROFILES_DIR)
        step = find_step(loaded.production, UNIQUE_PRODUCER, ContentType.VIDEO)
        sources = plan_next(data_dir, loaded.language, loaded.name, count)
        for source in sources:
            _source_path(data_dir, source)
        started = self._seams.now or datetime.now(UTC)
        run_id = choose_run_id(settings.output_dir, [loaded], started)
        run_dir = settings.output_dir / loaded.language / profile_slug(loaded) / run_id
        report = _report(loaded, step, sources, count, run_id, started)
        write_report(run_dir, report)
        writer = self._seams.writer or TextClient(settings)
        checker = self._checker_for(step, settings)
        randomizer = random.Random() if self._seams.rng is None else self._seams.rng  # noqa: S311 — variety, not secrets
        generated, failed, total_bytes = _run_items(
            loaded,
            step,
            report,
            sources,
            data_dir,
            run_dir,
            writer,
            checker,
            randomizer,
        )
        close_report(report, generated, failed, total_bytes)
        write_report(run_dir, report)
        return ProducedBatch(
            run_id=run_id,
            generated=report.totals.generated,
            failed=report.totals.failed,
            run_dirs=(run_dir,),
        )

    def _checker_for(
        self,
        step: ProductionStep,
        settings: GeneratorSettings,
    ) -> FrameChecker | None:
        """Return the Clef checker when the step declares the source check.

        The check runs on the source frames before the card is drawn, so a
        rejected source never reaches the text model or the stock.
        """
        if "clef" not in step.qa:
            return None
        if self._seams.checker is not None:
            return self._seams.checker
        if not settings.safety_enabled:
            logger.bind(stage="safety").warning(
                "Проверка кадров Clef выключена: GENERATOR_SAFETY_ENABLED=false"
            )
            return None
        return FrameChecker(settings)


def _run_items(
    profile: Any,
    step: ProductionStep,
    report: RunReport,
    sources: tuple[SourceItem, ...],
    data_dir: Path,
    run_dir: Path,
    writer: TextWriter,
    checker: FrameChecker | None,
    rng: random.Random,
) -> tuple[int, int, int]:
    """Render, publish and count every source of one batch."""
    final_dir = run_dir / _FINAL_DIR
    generated = failed = total_bytes = 0
    for item, source in zip(report.items, sources, strict=True):
        try:
            size = _produce_item(
                profile,
                step,
                item,
                source,
                data_dir,
                run_dir,
                final_dir,
                writer,
                checker,
                rng,
            )
            mark_published(data_dir, source.id)
        except (
            GeneratorError,
            OSError,
            UnicodeError,
            MemoryError,
            OverflowError,
        ) as ex:
            failed += 1
            mark_failed(item, ex, "Исходник не уникализирован")
        else:
            generated += 1
            total_bytes += size
            item.status = RunStatus.DONE
            logger.bind(file=item.file, source=source.path).info("Уникальный ролик готов")
        write_report(run_dir, report)
    return generated, failed, total_bytes


def _source_path(data_dir: Path, source: SourceItem) -> Path:
    """Resolve one stored source path and keep it inside the data directory.

    Raises:
        GeneratorError: The path escapes the data directory or the file is
            gone; the batch then fails before the first render.
    """
    root = data_dir.resolve()
    path = (data_dir / source.path).resolve()
    if not path.is_relative_to(root):
        raise GeneratorError(f"путь исходника выходит за каталог данных: {source.path}")
    if not path.is_file():
        raise GeneratorError(
            f"исходник не найден на диске: {source.path}; запустите импорт sources"
        )
    return path


def _produce_item(
    profile: Any,
    step: ProductionStep,
    item: RunItem,
    source: SourceItem,
    data_dir: Path,
    run_dir: Path,
    final_dir: Path,
    writer: TextWriter,
    checker: FrameChecker | None,
    rng: random.Random,
) -> int:
    """Render one source, check its QA and publish the final mp4.

    Args:
        profile: Loaded profile with card style, music and stock niche.
        step: Recipe step with its declared QA checks.
        item: Report item that receives the result and duration.
        source: Pool row taken by the cursor.
        data_dir: Root ``app_data`` directory.
        run_dir: Directory of this run.
        final_dir: Directory that receives the final mp4.
        writer: Text model port.
        checker: Clef checker, or ``None`` when the check is disabled.
        rng: Random source for the card and music choice.

    Returns:
        Size of the final mp4 in bytes.

    Raises:
        QaError: The source or the final video failed a declared check.
        GeneratorError: The card or the publication failed.
        OSError: A file could not be read or written.
    """
    source_path = _source_path(data_dir, source)
    if checker is not None:
        reason = checker.verify(_LogItem(item.file), source_path, 1)
        if reason:
            raise QaError(reason)
    card = generate_overlay(profile, writer, rng=rng)
    save_overlay(run_dir / _TEXT_DIR / f"{Path(item.file).stem}.json", card)
    audio = rng.choice(profile.audio_tracks) if profile.audio_tracks else None
    final = final_dir / item.file
    mix(profile, card, source_path, final, audio)
    info = check_declared(
        step.qa,
        final,
        source.duration,
        expect_audio=bool(profile.audio_tracks),
    )
    publish_stock(profile, final, data_dir)
    item.duration = round(info.duration, 2)
    return final.stat().st_size


def _report(
    profile: Any,
    step: ProductionStep,
    sources: tuple[SourceItem, ...],
    count: int,
    run_id: str,
    started: datetime,
) -> RunReport:
    """Build the initial ``run.json`` report of one unique run."""
    slug = profile_slug(profile)
    items = [
        RunItem(
            file=f"{slug}_{run_id}_{seq:03d}.mp4",
            profile=profile.name,
            source_path=source.path,
            source_sha256=source.sha256,
            duration=source.duration,
            producer=step.producer,
            type=step.type,
            qa=list(step.qa),
        )
        for seq, source in enumerate(sources, start=1)
    ]
    return RunReport(
        run_id=run_id,
        started_at=started,
        status=RunStatus.RUNNING,
        profiles=[profile.name],
        count=count,
        production=[
            RunStep(producer=entry.producer, type=entry.type, qa=list(entry.qa))
            for entry in profile.production
        ],
        items=items,
    )


__all__ = ["UniqueProducer"]
