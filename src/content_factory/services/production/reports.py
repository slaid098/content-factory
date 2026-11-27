"""``run.json`` report of one profile built from a pipeline result.

The flow owns the report: producers return artifacts, and this module turns
the produced and accepted artifacts of every layer into the same report the
old per-producer path wrote.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from content_factory.exceptions.generator import GeneratorError
from content_factory.schemas.generator.pipeline import (
    Artifact,
    ItemFailure,
    Layer,
    PipelineContext,
    PipelineResult,
    content_type,
)
from content_factory.schemas.generator.run import (
    ProfileRef,
    RunItem,
    RunReport,
    RunServer,
    RunStatus,
    RunStep,
    RunTotals,
)
from content_factory.utils.fields import optional_int, optional_text


def build_report(
    profile: ProfileRef,
    layers: Sequence[Layer],
    context: PipelineContext,
    result: PipelineResult,
) -> RunReport:
    """Build the report of *profile* from the outcome of every layer.

    Args:
        profile: Profile whose items are selected from *result*.
        layers: Recipe layers in execution order.
        context: Run inputs; ``dry_run`` keeps every item planned.
        result: Outcome of the pipeline run of the profile group.

    Returns:
        A report with every produced item of every layer, including failed
        items with the reason of their layer.
    """
    by_name = {layer.name: layer for layer in layers}
    failures = _failures_by_layer(result)
    items: list[RunItem] = []
    accepted: list[Artifact] = []
    server: RunServer | None = None
    for outcome in result.layers:
        layer = _layer(by_name, outcome.layer)
        accepted_keys = {
            (artifact.profile, artifact.item)
            for artifact in outcome.artifacts
            if artifact.profile == profile.name
        }
        reported: set[tuple[str, str]] = set()
        for artifact in outcome.produced:
            if artifact.profile != profile.name:
                continue
            key = (artifact.profile, artifact.item)
            reported.add(key)
            failure = failures.get(outcome.layer, {}).get(key)
            item = _item(artifact, layer, context.dry_run, key in accepted_keys, failure)
            items.append(item)
            server = server or _server(artifact.meta)
            if item.status is RunStatus.DONE:
                accepted.append(artifact)
        for (name, item_name), failure in failures.get(outcome.layer, {}).items():
            if name != profile.name or (name, item_name) in reported:
                continue
            items.append(_failure_item(layer, failure))
    return RunReport(
        run_id=context.run_id,
        started_at=context.now,
        finished_at=None if context.dry_run else datetime.now(UTC),
        status=_status(context.dry_run, items),
        profiles=[profile.name],
        count=context.count,
        production=[
            RunStep(
                producer=layer.producer,
                type=content_type(layer.output),
                qa=list(layer.checks),
            )
            for layer in layers
        ],
        server=server,
        items=items,
        totals=RunTotals() if context.dry_run else _totals(accepted, items),
    )


def _failures_by_layer(
    result: PipelineResult,
) -> dict[str, dict[tuple[str, str], ItemFailure]]:
    """Index the failures of every layer by profile and item."""
    return {
        outcome.layer: {(failure.profile, failure.item): failure for failure in outcome.failures}
        for outcome in result.layers
    }


def _layer(by_name: Mapping[str, Layer], name: str) -> Layer:
    """Return the recipe layer of *name*.

    Raises:
        GeneratorError: The pipeline reported a layer the recipe does not hold.
    """
    try:
        return by_name[name]
    except KeyError as ex:
        raise GeneratorError(f"конвейер вернул неизвестный слой {name!r}") from ex


def _item(
    artifact: Artifact,
    layer: Layer,
    dry_run: bool,
    passed: bool,
    failure: ItemFailure | None,
) -> RunItem:
    """Turn one produced artifact into a report item."""
    meta = artifact.meta
    if dry_run:
        status, error = RunStatus.DRY_RUN, None
    elif passed:
        status, error = RunStatus.DONE, None
    else:
        status = RunStatus.FAILED
        error = failure.reason if failure is not None else _meta_error(artifact)
    return RunItem(
        file=artifact.item,
        profile=artifact.profile,
        look_id=optional_text(meta.get("look_id")),
        seed=optional_int(meta.get("seed")),
        frames=optional_int(meta.get("frames")),
        duration=_number(meta, "duration"),
        status=status,
        error=error,
        producer=layer.producer,
        type=content_type(layer.output),
        qa=list(layer.checks),
    )


def _failure_item(layer: Layer, failure: ItemFailure) -> RunItem:
    """Build a failed item that a producer did not return at all."""
    return RunItem(
        file=failure.item,
        profile=failure.profile,
        status=RunStatus.FAILED,
        error=failure.reason,
        producer=layer.producer,
        type=content_type(layer.output),
        qa=list(layer.checks),
    )


def _status(dry_run: bool, items: Sequence[RunItem]) -> RunStatus:
    """Return the report status of one profile."""
    if dry_run:
        return RunStatus.DRY_RUN
    failed = any(item.status is RunStatus.FAILED for item in items)
    return RunStatus.FAILED if failed else RunStatus.DONE


def _totals(accepted: Sequence[Artifact], items: Sequence[RunItem]) -> RunTotals:
    """Count finished, failed and written bytes of one profile."""
    generated = sum(1 for item in items if item.status is RunStatus.DONE)
    failed = sum(1 for item in items if item.status is RunStatus.FAILED)
    size = sum(_size(artifact.path) for artifact in accepted)
    return RunTotals(generated=generated, failed=failed, bytes=size)


def _size(path: Path) -> int:
    """Return the size of a finished file, or zero when it is missing."""
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _meta_error(artifact: Artifact) -> str:
    """Return the producer reason of a rejected artifact."""
    reason = artifact.meta.get("error")
    return str(reason) if reason else f"item {artifact.item} не прошёл проверку слоя"


def _server(meta: Mapping[str, object]) -> RunServer | None:
    """Build the server block stored by a producer, if any."""
    raw = meta.get("server")
    if not isinstance(raw, Mapping):
        return None
    return RunServer.model_validate(dict(raw))


def _number(meta: Mapping[str, object], key: str) -> float | None:
    """Return an optional number field."""
    value = meta.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


__all__ = ["build_report"]
