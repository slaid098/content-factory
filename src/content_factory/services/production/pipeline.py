"""Engine of the layered production pipeline.

A layer takes the artifacts of the layers named as its inputs, produces new
artifacts and passes each of them through its checks. Only accepted artifacts
reach the following layers. The engine knows the contracts and the registries
only: it has no branches for a producer, a check, a topic or a content type.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from content_factory.exceptions.generator import GeneratorError, ProfileError
from content_factory.schemas.generator.pipeline import (
    Artifact,
    ItemFailure,
    Layer,
    LayerCheck,
    LayerOutcome,
    LayerProducer,
    PipelineContext,
    PipelineResult,
)
from content_factory.services.production.qa import CheckRegistry
from content_factory.services.production.registry import ProducerRegistry

_ItemKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class _ResolvedLayer:
    """A layer with its producer and checks resolved from the registries."""

    layer: Layer
    producer: LayerProducer
    checks: tuple[tuple[str, LayerCheck], ...]


def run_pipeline(
    context: PipelineContext,
    layers: Sequence[Layer],
    *,
    producers: ProducerRegistry | None = None,
    checks: CheckRegistry | None = None,
) -> PipelineResult:
    """Run *layers* in order and return the outcome of every layer.

    The recipe is validated before the first layer runs. In a real run a
    layer gets the artifacts of the layers listed in its ``inputs`` and its
    checks run on every artifact in the declared order; the first failed check
    removes the item from the following layers. In a dry run every producer
    only plans its layer, checks do not run and nothing is written.

    Args:
        context: Profiles, count and runtime objects shared by every layer.
        layers: Recipe layers in execution order.
        producers: Registry of layer producers; an empty one when omitted.
        checks: Registry of layer checks; an empty one when omitted.

    Returns:
        Outcome of every layer, the artifacts of the last layer and every
        item failure in layer order.

    Raises:
        ProfileError: The recipe is empty or malformed: a repeated name, an
            input that is not a previous layer, an unknown producer or check,
            or an output the producer does not support.
        GeneratorError: A producer or check breaks the contract or fails
            while running.
    """
    resolved = _resolve_layers(context, layers, producers, checks)
    outcomes: list[LayerOutcome] = []
    by_name: dict[str, LayerOutcome] = {}
    failures: list[ItemFailure] = []
    failed: set[_ItemKey] = set()
    for item in resolved:
        if context.dry_run:
            artifacts = _plan_layer(item, context)
            item_failures: tuple[ItemFailure, ...] = ()
            produced = artifacts
        else:
            inputs = _inputs_of(item.layer, by_name, failed)
            produced, artifacts, item_failures = _run_layer(item, context, inputs)
            artifacts = _without_failed(artifacts, failed)
        outcome = LayerOutcome(
            layer=item.layer.name,
            artifacts=artifacts,
            failures=item_failures,
            produced=produced,
        )
        outcomes.append(outcome)
        by_name[item.layer.name] = outcome
        failures.extend(item_failures)
        failed.update((failure.profile, failure.item) for failure in item_failures)
    return PipelineResult(
        layers=tuple(outcomes),
        artifacts=_without_failed(outcomes[-1].artifacts, failed),
        failures=tuple(failures),
    )


def _resolve_layers(
    context: PipelineContext,
    layers: Sequence[Layer],
    producers: ProducerRegistry | None,
    checks: CheckRegistry | None,
) -> tuple[_ResolvedLayer, ...]:
    """Validate the whole recipe and resolve its producers and checks."""
    if not layers:
        raise ProfileError("конвейер не содержит слоёв")
    _validate_context(context)
    producer_registry = producers if producers is not None else ProducerRegistry()
    check_registry = checks if checks is not None else CheckRegistry()
    names: set[str] = set()
    resolved: list[_ResolvedLayer] = []
    for layer in layers:
        _validate_layer_name(layer, names)
        for source in layer.inputs:
            if source not in names:
                raise ProfileError(
                    f"слой {layer.name!r}: вход {source!r} не является предыдущим слоем"
                )
        producer = producer_registry.resolve(layer.producer)
        if layer.output not in producer.supported_outputs:
            raise ProfileError(
                f"слой {layer.name!r}: producer {layer.producer!r} "
                f"не поддерживает выход {layer.output.value!r}"
            )
        layer_checks = tuple((name, check_registry.resolve(name)) for name in layer.checks)
        names.add(layer.name)
        resolved.append(_ResolvedLayer(layer, producer, layer_checks))
    return tuple(resolved)


def _validate_context(context: PipelineContext) -> None:
    """Reject a run without profiles or with a non-positive item count."""
    if not context.profiles:
        raise ProfileError("конвейер не содержит профилей")
    if context.count <= 0:
        raise ProfileError(f"count должен быть больше нуля, получено {context.count!r}")


def _validate_layer_name(layer: Layer, names: set[str]) -> None:
    """Reject a blank or already used layer name."""
    if not layer.name.strip():
        raise ProfileError("пустое имя слоя")
    if layer.name in names:
        raise ProfileError(f"повтор имени слоя {layer.name!r}")


def _inputs_of(
    layer: Layer,
    outcomes: dict[str, LayerOutcome],
    failed: set[_ItemKey],
) -> tuple[Artifact, ...]:
    """Collect the artifacts of the declared inputs, dropping failed items."""
    inputs: list[Artifact] = []
    for source in layer.inputs:
        inputs.extend(
            artifact
            for artifact in outcomes[source].artifacts
            if (artifact.profile, artifact.item) not in failed
        )
    return tuple(inputs)


def _without_failed(
    artifacts: tuple[Artifact, ...],
    failed: set[_ItemKey],
) -> tuple[Artifact, ...]:
    """Drop artifacts whose item already failed in an earlier layer."""
    return tuple(
        artifact for artifact in artifacts if (artifact.profile, artifact.item) not in failed
    )


def _plan_layer(item: _ResolvedLayer, context: PipelineContext) -> tuple[Artifact, ...]:
    """Ask the producer for the plan of one layer and validate its artifacts."""
    try:
        planned = tuple(item.producer.plan_layer(item.layer, context))
    except GeneratorError:
        raise
    except Exception as ex:
        raise GeneratorError(
            f"слой {item.layer.name!r}: producer {item.layer.producer!r} не построил план: {ex}"
        ) from ex
    _validate_artifacts(item.layer, planned, context)
    return planned


def _run_layer(
    item: _ResolvedLayer,
    context: PipelineContext,
    inputs: tuple[Artifact, ...],
) -> tuple[tuple[Artifact, ...], tuple[Artifact, ...], tuple[ItemFailure, ...]]:
    """Produce, validate and check one layer of a real run."""
    produced = _produce_layer(item, context, inputs)
    _validate_artifacts(item.layer, produced, context)
    missing = _missing_artifacts(item.layer, produced, inputs, context)
    kept, check_failures = _run_checks(item, context, produced)
    return produced, kept, (*missing, *check_failures)


def _produce_layer(
    item: _ResolvedLayer,
    context: PipelineContext,
    inputs: tuple[Artifact, ...],
) -> tuple[Artifact, ...]:
    """Call the producer of one layer and keep its contract errors intact."""
    try:
        return tuple(item.producer.produce_layer(item.layer, inputs, context))
    except GeneratorError:
        raise
    except Exception as ex:
        raise GeneratorError(
            f"слой {item.layer.name!r}: producer {item.layer.producer!r} упал: {ex}"
        ) from ex


def _validate_artifacts(
    layer: Layer,
    artifacts: tuple[Artifact, ...],
    context: PipelineContext,
) -> None:
    """Reject artifacts that break the kind, identity or uniqueness contract."""
    profiles = {profile.name for profile in context.profiles}
    seen: set[_ItemKey] = set()
    for artifact in artifacts:
        if not artifact.item:
            raise GeneratorError(
                f"слой {layer.name!r}: producer {layer.producer!r} вернул артефакт без item"
            )
        if artifact.kind is not layer.output:
            raise GeneratorError(
                f"слой {layer.name!r}: producer {layer.producer!r} вернул "
                f"{artifact.kind.value!r}, ожидался {layer.output.value!r}"
            )
        if artifact.profile not in profiles:
            raise GeneratorError(
                f"слой {layer.name!r}: артефакт чужого профиля {artifact.profile!r}"
            )
        key = (artifact.profile, artifact.item)
        if key in seen:
            raise GeneratorError(
                f"слой {layer.name!r}: повтор артефакта item {artifact.item!r} "
                f"профиля {artifact.profile!r}"
            )
        seen.add(key)


def _missing_artifacts(
    layer: Layer,
    artifacts: tuple[Artifact, ...],
    inputs: tuple[Artifact, ...],
    context: PipelineContext,
) -> tuple[ItemFailure, ...]:
    """Check the expected item set of one layer and report absent items."""
    if not layer.inputs:
        _require_counts(layer, artifacts, context)
        return ()
    expected = _unique_keys(inputs)
    expected_set = set(expected)
    produced = {(artifact.profile, artifact.item) for artifact in artifacts}
    for artifact in artifacts:
        if (artifact.profile, artifact.item) not in expected_set:
            raise GeneratorError(
                f"слой {layer.name!r}: producer {layer.producer!r} вернул чужой item "
                f"{artifact.item!r} профиля {artifact.profile!r}"
            )
    return tuple(
        ItemFailure(
            item=item,
            profile=profile,
            layer=layer.name,
            reason=f"producer {layer.producer!r} не вернул артефакт",
        )
        for profile, item in expected
        if (profile, item) not in produced
    )


def _require_counts(
    layer: Layer, artifacts: tuple[Artifact, ...], context: PipelineContext
) -> None:
    """Require the first layer to produce exactly *count* artifacts per profile."""
    profiles = tuple(dict.fromkeys(profile.name for profile in context.profiles))
    counts = Counter(artifact.profile for artifact in artifacts)
    for profile in profiles:
        actual = counts[profile]
        if actual != context.count:
            raise GeneratorError(
                f"слой {layer.name!r}: профиль {profile!r} дал {actual} артефактов, "
                f"ожидалось {context.count}"
            )


def _unique_keys(artifacts: Sequence[Artifact]) -> tuple[_ItemKey, ...]:
    """Return the unique ``(profile, item)`` keys in their original order."""
    keys: dict[_ItemKey, None] = {}
    for artifact in artifacts:
        keys[(artifact.profile, artifact.item)] = None
    return tuple(keys)


def _run_checks(
    item: _ResolvedLayer,
    context: PipelineContext,
    artifacts: tuple[Artifact, ...],
) -> tuple[tuple[Artifact, ...], tuple[ItemFailure, ...]]:
    """Run the layer checks on every artifact and split accepted artifacts."""
    kept: list[Artifact] = []
    failures: list[ItemFailure] = []
    for artifact in artifacts:
        reason = _first_failure(item, context, artifact)
        if reason is None:
            kept.append(artifact)
            continue
        failures.append(
            ItemFailure(
                item=artifact.item,
                profile=artifact.profile,
                layer=item.layer.name,
                reason=reason,
            )
        )
    return tuple(kept), tuple(failures)


def _first_failure(
    item: _ResolvedLayer,
    context: PipelineContext,
    artifact: Artifact,
) -> str | None:
    """Return the reason of the first failed check, or ``None`` when all pass."""
    for name, check in item.checks:
        try:
            result = check.check(item.layer, artifact, context)
        except GeneratorError:
            raise
        except Exception as ex:
            raise GeneratorError(f"слой {item.layer.name!r}: проверка {name!r} упала: {ex}") from ex
        if not result.passed:
            return result.reason or f"проверка {name!r} не прошла"
    return None


__all__ = ["run_pipeline"]
