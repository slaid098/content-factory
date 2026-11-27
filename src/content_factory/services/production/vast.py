"""Layer adapter of the existing Vast.ai/ComfyUI batch pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import ValidationError

from content_factory.constants import PROFILES_DIR
from content_factory.exceptions.generator import GeneratorError
from content_factory.schemas.generator.pipeline import (
    Artifact,
    ArtifactKind,
    Layer,
    PipelineContext,
)
from content_factory.schemas.generator.run import RunItem, RunReport, RunServer, RunStatus
from content_factory.services.generator import (
    generate_batch,
    load_profile,
    plan_batch,
    profile_slug,
    read_profile_cursor,
)
from content_factory.services.generator.service import PlannedItem, planned_item
from content_factory.services.production.paths import context_run_dir
from content_factory.utils.fields import optional_int, optional_text

_FINAL_DIR = "final"
_RUN_FILENAME = "run.json"
_VIDEO_MEDIA_TYPE = "video/mp4"


class VastProducer:
    """Run the existing generator pipeline as the video layer of a group."""

    supported_outputs = frozenset({ArtifactKind.VIDEO})

    def plan_layer(self, layer: Layer, context: PipelineContext) -> tuple[Artifact, ...]:
        """Plan looks, seeds and lengths of every profile of the group."""
        del layer
        artifacts: list[Artifact] = []
        for profile in context.profiles:
            loaded = load_profile(profile.name, context.data_dir / PROFILES_DIR)
            run_dir = context_run_dir(context, loaded.language, profile_slug(loaded))
            cursor = read_profile_cursor(
                context.settings.output_dir,
                loaded.language,
                profile_slug(loaded),
            )
            for item in plan_batch(
                loaded,
                context.count,
                context.run_id,
                rng=context.rng,
                start_after=cursor,
            ):
                artifacts.append(
                    Artifact(
                        item=item.filename,
                        profile=loaded.name,
                        kind=ArtifactKind.VIDEO,
                        path=run_dir / _FINAL_DIR / item.filename,
                        media_type=_VIDEO_MEDIA_TYPE,
                        meta={
                            "look_id": item.look.id,
                            "seed": item.seed,
                            "frames": item.frames,
                            "status": "planned",
                        },
                    )
                )
        return tuple(artifacts)

    def produce_layer(
        self,
        layer: Layer,
        inputs: tuple[Artifact, ...],
        context: PipelineContext,
    ) -> tuple[Artifact, ...]:
        """Generate the whole group with one ``generate_batch`` call."""
        del inputs
        names = ",".join(profile.name for profile in context.profiles)
        plan = _planned_items(context, layer)
        extras: dict[str, Any] = {}
        if context.journal is not None:
            extras["journal"] = context.journal
        if plan is not None:
            extras["plan"] = plan
        generate_batch(
            profiles=names,
            count=context.count,
            data_dir=context.data_dir,
            settings=context.settings,
            run_id=context.run_id,
            rng=context.rng,
            now=context.now,
            **extras,
        )
        return tuple(self._artifacts(context, layer))

    def _artifacts(self, context: PipelineContext, layer: Layer) -> list[Artifact]:
        """Describe planned items; the report only supplies statuses and errors."""
        artifacts: list[Artifact] = []
        planned_by_profile = _planned_by_profile(context, layer)
        for profile in context.profiles:
            loaded = load_profile(profile.name, context.data_dir / PROFILES_DIR)
            run_dir = context_run_dir(context, loaded.language, profile_slug(loaded))
            report = _read_report(run_dir / _RUN_FILENAME)
            reported, server = _trusted_report(report, context.run_id, loaded.name)
            for planned in planned_by_profile.get(loaded.name, ()):
                artifacts.append(
                    _artifact(
                        loaded.name,
                        run_dir,
                        planned,
                        reported.get(planned.item),
                        server,
                        loaded.fps,
                        bool(loaded.audio_tracks),
                    )
                )
        return artifacts


def _planned_items(context: PipelineContext, layer: Layer) -> tuple[PlannedItem, ...] | None:
    """Convert the journal plan of a layer into planned videos, when present."""
    artifacts = context.planned.get(layer.name)
    if artifacts is None:
        return None
    items: list[PlannedItem] = []
    for profile in context.profiles:
        loaded = load_profile(profile.name, context.data_dir / PROFILES_DIR)
        own = [artifact for artifact in artifacts if artifact.profile == loaded.name]
        for seq, artifact in enumerate(own, start=1):
            items.append(
                planned_item(
                    loaded,
                    artifact.item,
                    look_id=optional_text(artifact.meta.get("look_id")),
                    seed=optional_int(artifact.meta.get("seed")),
                    frames=optional_int(artifact.meta.get("frames")),
                    seq=seq,
                )
            )
    return tuple(items)


def _planned_by_profile(
    context: PipelineContext,
    layer: Layer,
) -> dict[str, tuple[Artifact, ...]]:
    """Return the planned artifacts of every profile, rebuilding them if absent."""
    planned = context.planned.get(layer.name)
    if planned is not None:
        return {
            profile.name: tuple(a for a in planned if a.profile == profile.name)
            for profile in context.profiles
        }
    rebuilt: dict[str, tuple[Artifact, ...]] = {}
    for profile in context.profiles:
        loaded = load_profile(profile.name, context.data_dir / PROFILES_DIR)
        run_dir = context_run_dir(context, loaded.language, profile_slug(loaded))
        cursor = read_profile_cursor(
            context.settings.output_dir,
            loaded.language,
            profile_slug(loaded),
        )
        rebuilt[loaded.name] = tuple(
            Artifact(
                item=item.filename,
                profile=loaded.name,
                kind=ArtifactKind.VIDEO,
                path=run_dir / _FINAL_DIR / item.filename,
                media_type=_VIDEO_MEDIA_TYPE,
                meta={
                    "look_id": item.look.id,
                    "seed": item.seed,
                    "frames": item.frames,
                    "status": "planned",
                },
            )
            for item in plan_batch(
                loaded,
                context.count,
                context.run_id,
                rng=context.rng,
                start_after=cursor,
            )
        )
    return rebuilt


def _trusted_report(
    report: RunReport, run_id: str, profile: str
) -> tuple[dict[str, RunItem], RunServer | None]:
    """Index the trusted report items of *profile* by their plain file name."""
    if report.run_id != run_id or report.profiles != [profile]:
        logger.bind(profile=profile, report_run_id=report.run_id, run_id=run_id).warning(
            "Отчёт генератора принадлежит другому прогону: items не приняты"
        )
        return {}, None
    items: dict[str, RunItem] = {}
    for item in report.items:
        if item.profile != profile or not _plain_name(item.file):
            logger.bind(profile=profile, file=item.file, item_profile=item.profile).warning(
                "Отчёт генератора называет чужой профиль или путь: item не принят"
            )
            continue
        items[item.file] = item
    return items, report.server


def _artifact(
    profile: str,
    run_dir: Path,
    planned: Artifact,
    item: RunItem | None,
    server: RunServer | None,
    fps: int,
    expect_audio: bool,
) -> Artifact:
    """Describe one planned video, using its report item when it matches."""
    filename = planned.item
    frames = item.frames if item is not None else optional_int(planned.meta.get("frames"))
    look_id = item.look_id if item is not None else optional_text(planned.meta.get("look_id"))
    seed = item.seed if item is not None else optional_int(planned.meta.get("seed"))
    if item is None:
        status, error = "failed", "нет отчёта генератора"
    elif item.status is RunStatus.DONE:
        status, error = "done", None
    else:
        status, error = "failed", item.error
    return Artifact(
        item=filename,
        profile=profile,
        kind=ArtifactKind.VIDEO,
        path=_final_path(run_dir, filename),
        media_type=_VIDEO_MEDIA_TYPE,
        meta={
            "look_id": look_id,
            "seed": seed,
            "frames": frames,
            "duration": item.duration if item is not None else None,
            "status": status,
            "error": error,
            "expected_seconds": frames / fps if frames and fps > 0 else None,
            "expect_audio": expect_audio,
            "server": server.model_dump() if server is not None else None,
        },
    )


def _plain_name(filename: str) -> bool:
    """Return whether *filename* is one plain file name, not a path."""
    return bool(filename) and filename not in (".", "..") and Path(filename).name == filename


def _final_path(run_dir: Path, filename: str) -> Path:
    """Return the final path of *filename*, checked to stay inside ``final``.

    Raises:
        GeneratorError: The name is not a plain file inside the final directory.
    """
    final_dir = (run_dir / _FINAL_DIR).resolve()
    if not _plain_name(filename) or not (final_dir / filename).resolve().is_relative_to(final_dir):
        raise GeneratorError(f"недопустимый файл генератора: {filename!r}")
    return run_dir / _FINAL_DIR / filename


def _read_report(path: Path) -> RunReport:
    """Read the report written by ``generate_batch``.

    Raises:
        GeneratorError: The report is missing or malformed.
    """
    try:
        return RunReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as ex:
        raise GeneratorError(f"не удалось прочитать отчёт генератора {path}: {ex}") from ex


__all__ = ["VastProducer"]
