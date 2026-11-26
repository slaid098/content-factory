"""Layered production pipeline: chain, checks, dry run and validation."""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from content_factory.config.settings import GeneratorSettings
from content_factory.exceptions.generator import GeneratorError, ProfileError
from content_factory.schemas.generator.pipeline import (
    Artifact,
    ArtifactKind,
    CheckResult,
    ItemFailure,
    Layer,
    PipelineContext,
)
from content_factory.services.production import run_pipeline
from content_factory.services.production.qa import CheckRegistry
from content_factory.services.production.registry import ProducerRegistry


@dataclass(frozen=True, slots=True)
class _Profile:
    """Minimal profile satisfying the ``ProfileRef`` port."""

    name: str


class _FakeProducer:
    """Producer that plans items by the context and transforms its inputs."""

    def __init__(self, output: ArtifactKind, events: list[str] | None = None) -> None:
        self.supported_outputs = frozenset({output})
        self.events = events if events is not None else []
        self.plan_calls: list[Layer] = []
        self.produce_calls: list[tuple[Layer, tuple[Artifact, ...]]] = []

    def plan_layer(self, layer: Layer, context: PipelineContext) -> tuple[Artifact, ...]:
        """Plan one artifact per item of the context."""
        self.plan_calls.append(layer)
        return tuple(
            _artifact(layer.output, item, profile) for profile, item in _plan_keys(context)
        )

    def produce_layer(
        self,
        layer: Layer,
        inputs: tuple[Artifact, ...],
        context: PipelineContext,
    ) -> tuple[Artifact, ...]:
        """Produce one artifact per input item, or per context item at the root."""
        self.events.append(f"produce:{layer.name}")
        self.produce_calls.append((layer, inputs))
        keys = _input_keys(inputs) if layer.inputs else _plan_keys(context)
        return tuple(_artifact(layer.output, item, profile) for profile, item in keys)


class _FixedProducer:
    """Producer that always returns the same artifacts."""

    def __init__(self, output: ArtifactKind, artifacts: tuple[Artifact, ...]) -> None:
        self.supported_outputs = frozenset({output})
        self._artifacts = artifacts

    def plan_layer(self, layer: Layer, context: PipelineContext) -> tuple[Artifact, ...]:
        """Return the fixed artifacts."""
        return self._artifacts

    def produce_layer(
        self,
        layer: Layer,
        inputs: tuple[Artifact, ...],
        context: PipelineContext,
    ) -> tuple[Artifact, ...]:
        """Return the fixed artifacts."""
        return self._artifacts


class _RaisingProducer:
    """Producer that fails while running."""

    supported_outputs = frozenset({ArtifactKind.VIDEO})

    def plan_layer(self, layer: Layer, context: PipelineContext) -> tuple[Artifact, ...]:
        """Fail before planning."""
        raise ValueError("план сломан")

    def produce_layer(
        self,
        layer: Layer,
        inputs: tuple[Artifact, ...],
        context: PipelineContext,
    ) -> tuple[Artifact, ...]:
        """Fail before producing."""
        raise ValueError("генерация сломана")


class _FakeCheck:
    """Check that records calls and answers from a per-item table."""

    def __init__(
        self,
        name: str,
        *,
        events: list[str] | None = None,
        results: Mapping[str, CheckResult] | None = None,
    ) -> None:
        self.name = name
        self.events = events if events is not None else []
        self.results = dict(results or {})
        self.calls: list[tuple[str, str]] = []

    def check(self, layer: Layer, artifact: Artifact, context: PipelineContext) -> CheckResult:
        """Record the call and return the configured verdict."""
        self.events.append(f"check:{layer.name}:{self.name}:{artifact.item}")
        self.calls.append((layer.name, artifact.item))
        return self.results.get(artifact.item, CheckResult(passed=True))


class _RaisingCheck:
    """Check that fails while running."""

    def check(self, layer: Layer, artifact: Artifact, context: PipelineContext) -> CheckResult:
        """Fail before returning a verdict."""
        raise ValueError("проверка сломана")


class _GeneratorErrorProducer:
    """Producer that raises a domain error of its own."""

    supported_outputs = frozenset({ArtifactKind.VIDEO})

    def plan_layer(self, layer: Layer, context: PipelineContext) -> tuple[Artifact, ...]:
        """Raise a domain error while planning."""
        raise GeneratorError("своя ошибка плана")

    def produce_layer(
        self,
        layer: Layer,
        inputs: tuple[Artifact, ...],
        context: PipelineContext,
    ) -> tuple[Artifact, ...]:
        """Raise a domain error while producing."""
        raise GeneratorError("своя ошибка генерации")


class _GeneratorErrorCheck:
    """Check that raises a domain error of its own."""

    def check(self, layer: Layer, artifact: Artifact, context: PipelineContext) -> CheckResult:
        """Raise a domain error instead of a verdict."""
        raise GeneratorError("своя ошибка проверки")


def _artifact(
    kind: ArtifactKind,
    item: str,
    profile: str = "dating/profile_1",
) -> Artifact:
    return Artifact(
        item=item,
        profile=profile,
        kind=kind,
        path=Path("/pipeline") / profile / f"{item}.{kind.value}",
        media_type=None,
        meta={},
    )


def _plan_keys(context: PipelineContext) -> tuple[tuple[str, str], ...]:
    return tuple(
        (profile.name, f"{profile.name}:{index}")
        for profile in context.profiles
        for index in range(1, context.count + 1)
    )


def _input_keys(inputs: tuple[Artifact, ...]) -> tuple[tuple[str, str], ...]:
    keys: dict[tuple[str, str], None] = {}
    for artifact in inputs:
        keys[(artifact.profile, artifact.item)] = None
    return tuple(keys)


def _context(
    tmp_path: Path,
    *,
    profiles: tuple[str, ...] = ("dating/profile_1",),
    count: int = 2,
    dry_run: bool = False,
) -> PipelineContext:
    return PipelineContext(
        profiles=tuple(_Profile(name) for name in profiles),
        count=count,
        data_dir=tmp_path,
        settings=GeneratorSettings(output_dir=tmp_path),
        run_id="20260101T000000Z",
        rng=random.Random(7),
        now=datetime(2026, 1, 1, tzinfo=UTC),
        dry_run=dry_run,
    )


def _source_layer(
    *,
    name: str = "source",
    producer: str = "video",
    checks: tuple[str, ...] = (),
) -> Layer:
    return Layer(name=name, producer=producer, inputs=(), output=ArtifactKind.VIDEO, checks=checks)


def test_chain_runs_layers_in_order_and_links_inputs(tmp_path: Path) -> None:
    events: list[str] = []
    source = _FakeProducer(ArtifactKind.VIDEO, events)
    caption = _FakeProducer(ArtifactKind.TEXT, events)
    render = _FakeProducer(ArtifactKind.VIDEO, events)
    producers = ProducerRegistry(
        {
            "source": lambda: source,
            "caption": lambda: caption,
            "render": lambda: render,
        }
    )
    layers = (
        Layer("source", "source", (), ArtifactKind.VIDEO),
        Layer("caption", "caption", ("source",), ArtifactKind.TEXT),
        Layer("render", "render", ("source", "caption"), ArtifactKind.VIDEO),
    )

    result = run_pipeline(_context(tmp_path, count=2), layers, producers=producers)

    assert [outcome.layer for outcome in result.layers] == ["source", "caption", "render"]
    assert [len(outcome.artifacts) for outcome in result.layers] == [2, 2, 2]
    assert caption.produce_calls[0][1] == result.layers[0].artifacts
    assert render.produce_calls[0][1] == (result.layers[0].artifacts + result.layers[1].artifacts)
    assert result.artifacts == result.layers[-1].artifacts
    assert result.failures == ()
    assert events == ["produce:source", "produce:caption", "produce:render"]


def test_checks_run_after_production_in_declared_order(tmp_path: Path) -> None:
    events: list[str] = []
    source = _FakeProducer(ArtifactKind.VIDEO, events)
    caption = _FakeProducer(ArtifactKind.TEXT, events)
    first = _FakeCheck("first", events=events)
    second = _FakeCheck("second", events=events)
    last = _FakeCheck("last", events=events)
    producers = ProducerRegistry({"video": lambda: source, "text": lambda: caption})
    checks = CheckRegistry({"first": lambda: first, "second": lambda: second, "last": lambda: last})
    layers = (
        Layer("source", "video", (), ArtifactKind.VIDEO, ("first", "second")),
        Layer("caption", "text", ("source",), ArtifactKind.TEXT, ("last",)),
    )

    run_pipeline(_context(tmp_path, count=2), layers, producers=producers, checks=checks)

    assert events == [
        "produce:source",
        "check:source:first:dating/profile_1:1",
        "check:source:second:dating/profile_1:1",
        "check:source:first:dating/profile_1:2",
        "check:source:second:dating/profile_1:2",
        "produce:caption",
        "check:caption:last:dating/profile_1:1",
        "check:caption:last:dating/profile_1:2",
    ]


def test_failed_item_is_removed_and_others_continue(tmp_path: Path) -> None:
    source = _FakeProducer(ArtifactKind.VIDEO)
    caption = _FakeProducer(ArtifactKind.TEXT)
    bad = _FakeCheck(
        "mp4",
        results={"dating/profile_1:2": CheckResult(False, "брак")},
    )
    producers = ProducerRegistry({"video": lambda: source, "text": lambda: caption})
    checks = CheckRegistry({"mp4": lambda: bad})
    layers = (
        Layer("source", "video", (), ArtifactKind.VIDEO, ("mp4",)),
        Layer("caption", "text", ("source",), ArtifactKind.TEXT),
    )

    result = run_pipeline(_context(tmp_path, count=3), layers, producers=producers, checks=checks)

    expected_failure = ItemFailure("dating/profile_1:2", "dating/profile_1", "source", "брак")
    assert [artifact.item for artifact in result.layers[0].artifacts] == [
        "dating/profile_1:1",
        "dating/profile_1:3",
    ]
    assert result.layers[0].failures == (expected_failure,)
    assert [artifact.item for artifact in caption.produce_calls[0][1]] == [
        "dating/profile_1:1",
        "dating/profile_1:3",
    ]
    assert len(result.artifacts) == 2
    assert result.failures == (expected_failure,)


def test_failed_item_does_not_return_through_another_input(tmp_path: Path) -> None:
    source = _FakeProducer(ArtifactKind.VIDEO)
    caption = _FakeProducer(ArtifactKind.TEXT)
    render = _FakeProducer(ArtifactKind.VIDEO)
    bad = _FakeCheck(
        "mp4",
        results={"dating/profile_1:2": CheckResult(False, "брак")},
    )
    producers = ProducerRegistry(
        {
            "video": lambda: source,
            "text": lambda: caption,
            "render": lambda: render,
        }
    )
    checks = CheckRegistry({"mp4": lambda: bad})
    layers = (
        Layer("source", "video", (), ArtifactKind.VIDEO),
        Layer("caption", "text", ("source",), ArtifactKind.TEXT, ("mp4",)),
        Layer("render", "render", ("source", "caption"), ArtifactKind.VIDEO),
    )

    result = run_pipeline(_context(tmp_path, count=3), layers, producers=producers, checks=checks)

    render_inputs = render.produce_calls[0][1]
    assert {artifact.item for artifact in render_inputs} == {
        "dating/profile_1:1",
        "dating/profile_1:3",
    }
    assert [artifact.item for artifact in result.layers[2].artifacts] == [
        "dating/profile_1:1",
        "dating/profile_1:3",
    ]
    assert [artifact.item for artifact in result.artifacts] == [
        "dating/profile_1:1",
        "dating/profile_1:3",
    ]
    assert result.failures == (
        ItemFailure("dating/profile_1:2", "dating/profile_1", "caption", "брак"),
    )


def test_missing_item_does_not_return_through_another_input(tmp_path: Path) -> None:
    source = _FakeProducer(ArtifactKind.VIDEO)
    caption = _FixedProducer(
        ArtifactKind.TEXT,
        (
            _artifact(ArtifactKind.TEXT, "dating/profile_1:1"),
            _artifact(ArtifactKind.TEXT, "dating/profile_1:3"),
        ),
    )
    render = _FakeProducer(ArtifactKind.VIDEO)
    producers = ProducerRegistry(
        {
            "video": lambda: source,
            "caption": lambda: caption,
            "render": lambda: render,
        }
    )
    layers = (
        Layer("source", "video", (), ArtifactKind.VIDEO),
        Layer("caption", "caption", ("source",), ArtifactKind.TEXT),
        Layer("render", "render", ("source", "caption"), ArtifactKind.VIDEO),
    )

    result = run_pipeline(_context(tmp_path, count=3), layers, producers=producers)

    render_inputs = render.produce_calls[0][1]
    assert {artifact.item for artifact in render_inputs} == {
        "dating/profile_1:1",
        "dating/profile_1:3",
    }
    assert [artifact.item for artifact in result.layers[2].artifacts] == [
        "dating/profile_1:1",
        "dating/profile_1:3",
    ]
    assert [artifact.item for artifact in result.artifacts] == [
        "dating/profile_1:1",
        "dating/profile_1:3",
    ]
    assert result.failures == (
        ItemFailure(
            "dating/profile_1:2",
            "dating/profile_1",
            "caption",
            "producer 'caption' не вернул артефакт",
        ),
    )


def test_first_failed_check_stops_the_other_checks_of_that_item(tmp_path: Path) -> None:
    source = _FakeProducer(ArtifactKind.VIDEO)
    bad = _FakeCheck("bad", results={"dating/profile_1:2": CheckResult(False, "брак")})
    later = _FakeCheck("later")
    producers = ProducerRegistry({"video": lambda: source})
    checks = CheckRegistry({"bad": lambda: bad, "later": lambda: later})
    layers = (_source_layer(checks=("bad", "later")),)

    result = run_pipeline(_context(tmp_path, count=2), layers, producers=producers, checks=checks)

    assert later.calls == [("source", "dating/profile_1:1")]
    assert result.layers[0].failures[0].reason == "брак"


def test_missing_check_reason_names_the_check(tmp_path: Path) -> None:
    source = _FakeProducer(ArtifactKind.VIDEO)
    quality = _FakeCheck("quality", results={"dating/profile_1:1": CheckResult(False)})
    producers = ProducerRegistry({"video": lambda: source})
    checks = CheckRegistry({"quality": lambda: quality})
    layers = (_source_layer(checks=("quality",)),)

    result = run_pipeline(_context(tmp_path, count=1), layers, producers=producers, checks=checks)

    assert result.failures[0].reason == "проверка 'quality' не прошла"


def test_dry_run_plans_layers_without_checks_or_writes(tmp_path: Path) -> None:
    events: list[str] = []
    source = _FakeProducer(ArtifactKind.VIDEO, events)
    caption = _FakeProducer(ArtifactKind.TEXT, events)
    mp4 = _FakeCheck("mp4", events=events)
    producers = ProducerRegistry({"video": lambda: source, "text": lambda: caption})
    checks = CheckRegistry({"mp4": lambda: mp4})
    layers = (
        Layer("source", "video", (), ArtifactKind.VIDEO, ("mp4",)),
        Layer("caption", "text", ("source",), ArtifactKind.TEXT, ("mp4",)),
    )

    result = run_pipeline(
        _context(tmp_path, count=2, dry_run=True),
        layers,
        producers=producers,
        checks=checks,
    )

    assert source.plan_calls == [layers[0]]
    assert caption.plan_calls == [layers[1]]
    assert source.produce_calls == []
    assert caption.produce_calls == []
    assert mp4.calls == []
    assert events == []
    assert [len(outcome.artifacts) for outcome in result.layers] == [2, 2]
    assert result.failures == ()
    assert list(tmp_path.iterdir()) == []


def test_empty_layers_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="не содержит слоёв"):
        run_pipeline(_context(tmp_path), ())


def test_empty_profiles_are_rejected(tmp_path: Path) -> None:
    producers = ProducerRegistry({"video": lambda: _FakeProducer(ArtifactKind.VIDEO)})

    with pytest.raises(ProfileError, match="не содержит профилей"):
        run_pipeline(
            _context(tmp_path, profiles=()),
            (_source_layer(),),
            producers=producers,
        )


@pytest.mark.parametrize("count", [0, -1])
def test_non_positive_count_is_rejected(tmp_path: Path, count: int) -> None:
    producers = ProducerRegistry({"video": lambda: _FakeProducer(ArtifactKind.VIDEO)})

    with pytest.raises(ProfileError, match="больше нуля"):
        run_pipeline(_context(tmp_path, count=count), (_source_layer(),), producers=producers)


def test_duplicate_layer_name_is_rejected(tmp_path: Path) -> None:
    producers = ProducerRegistry({"video": lambda: _FakeProducer(ArtifactKind.VIDEO)})

    with pytest.raises(ProfileError, match="повтор имени слоя"):
        run_pipeline(
            _context(tmp_path),
            (_source_layer(), _source_layer()),
            producers=producers,
        )


def test_blank_layer_name_is_rejected(tmp_path: Path) -> None:
    producers = ProducerRegistry({"video": lambda: _FakeProducer(ArtifactKind.VIDEO)})

    with pytest.raises(ProfileError, match="пустое имя слоя"):
        run_pipeline(
            _context(tmp_path),
            (_source_layer(name="  "),),
            producers=producers,
        )


def test_inputs_must_reference_a_previous_layer(tmp_path: Path) -> None:
    producers = ProducerRegistry({"text": lambda: _FakeProducer(ArtifactKind.TEXT)})
    layer = Layer("caption", "text", ("missing",), ArtifactKind.TEXT)

    with pytest.raises(ProfileError, match="не является предыдущим слоем"):
        run_pipeline(_context(tmp_path), (layer,), producers=producers)


def test_inputs_cannot_reference_a_future_layer(tmp_path: Path) -> None:
    producers = ProducerRegistry({"video": lambda: _FakeProducer(ArtifactKind.VIDEO)})
    layers = (
        Layer("first", "video", ("second",), ArtifactKind.VIDEO),
        _source_layer(name="second"),
    )

    with pytest.raises(ProfileError, match="не является предыдущим слоем"):
        run_pipeline(_context(tmp_path), layers, producers=producers)


def test_unknown_producer_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ProfileError, match="неизвестный producer"):
        run_pipeline(
            _context(tmp_path),
            (_source_layer(producer="absent"),),
            producers=ProducerRegistry(),
        )


def test_unknown_check_is_rejected(tmp_path: Path) -> None:
    producers = ProducerRegistry({"video": lambda: _FakeProducer(ArtifactKind.VIDEO)})

    with pytest.raises(ProfileError, match="неизвестная проверка"):
        run_pipeline(
            _context(tmp_path),
            (_source_layer(checks=("absent",)),),
            producers=producers,
            checks=CheckRegistry(),
        )


def test_output_must_be_supported_by_the_producer(tmp_path: Path) -> None:
    producers = ProducerRegistry({"video": lambda: _FakeProducer(ArtifactKind.VIDEO)})
    layer = Layer("source", "video", (), ArtifactKind.TEXT)

    with pytest.raises(ProfileError, match="не поддерживает выход"):
        run_pipeline(_context(tmp_path), (layer,), producers=producers)


def test_wrong_artifact_kind_is_a_contract_error(tmp_path: Path) -> None:
    producer = _FixedProducer(ArtifactKind.VIDEO, (_artifact(ArtifactKind.TEXT, "one"),))
    producers = ProducerRegistry({"video": lambda: producer})

    with pytest.raises(GeneratorError, match="вернул 'text'"):
        run_pipeline(_context(tmp_path, count=1), (_source_layer(),), producers=producers)


def test_empty_item_is_a_contract_error(tmp_path: Path) -> None:
    producer = _FixedProducer(ArtifactKind.VIDEO, (_artifact(ArtifactKind.VIDEO, ""),))
    producers = ProducerRegistry({"video": lambda: producer})

    with pytest.raises(GeneratorError, match="без item"):
        run_pipeline(_context(tmp_path, count=1), (_source_layer(),), producers=producers)


def test_artifact_of_a_foreign_profile_is_a_contract_error(tmp_path: Path) -> None:
    artifact = _artifact(ArtifactKind.VIDEO, "one", profile="other/profile")
    producer = _FixedProducer(ArtifactKind.VIDEO, (artifact,))
    producers = ProducerRegistry({"video": lambda: producer})

    with pytest.raises(GeneratorError, match="чужого профиля"):
        run_pipeline(_context(tmp_path, count=1), (_source_layer(),), producers=producers)


def test_duplicate_artifact_is_a_contract_error(tmp_path: Path) -> None:
    artifact = _artifact(ArtifactKind.VIDEO, "one")
    producer = _FixedProducer(ArtifactKind.VIDEO, (artifact, artifact))
    producers = ProducerRegistry({"video": lambda: producer})

    with pytest.raises(GeneratorError, match="повтор артефакта"):
        run_pipeline(_context(tmp_path, count=1), (_source_layer(),), producers=producers)


def test_first_layer_must_return_count_artifacts_per_profile(tmp_path: Path) -> None:
    producer = _FixedProducer(ArtifactKind.VIDEO, (_artifact(ArtifactKind.VIDEO, "one"),))
    producers = ProducerRegistry({"video": lambda: producer})

    with pytest.raises(GeneratorError, match="ожидалось 2"):
        run_pipeline(_context(tmp_path, count=2), (_source_layer(),), producers=producers)


def test_missing_input_item_is_a_failure_not_an_error(tmp_path: Path) -> None:
    source = _FakeProducer(ArtifactKind.VIDEO)
    caption = _FixedProducer(
        ArtifactKind.TEXT,
        (_artifact(ArtifactKind.TEXT, "dating/profile_1:1"),),
    )
    producers = ProducerRegistry({"video": lambda: source, "caption": lambda: caption})
    layers = (
        _source_layer(),
        Layer("caption", "caption", ("source",), ArtifactKind.TEXT),
    )

    result = run_pipeline(_context(tmp_path, count=2), layers, producers=producers)

    assert result.layers[1].failures == (
        ItemFailure(
            "dating/profile_1:2",
            "dating/profile_1",
            "caption",
            "producer 'caption' не вернул артефакт",
        ),
    )
    assert len(result.layers[1].artifacts) == 1
    assert len(result.artifacts) == 1


def test_artifact_of_an_unexpected_item_is_a_contract_error(tmp_path: Path) -> None:
    source = _FakeProducer(ArtifactKind.VIDEO)
    caption = _FixedProducer(
        ArtifactKind.TEXT,
        (_artifact(ArtifactKind.TEXT, "ghost"),),
    )
    producers = ProducerRegistry({"video": lambda: source, "caption": lambda: caption})
    layers = (
        _source_layer(),
        Layer("caption", "caption", ("source",), ArtifactKind.TEXT),
    )

    with pytest.raises(GeneratorError, match="чужой item 'ghost'"):
        run_pipeline(_context(tmp_path, count=1), layers, producers=producers)


def test_producer_error_is_wrapped_into_generator_error(tmp_path: Path) -> None:
    producers = ProducerRegistry({"video": _RaisingProducer})

    with pytest.raises(GeneratorError, match="генерация сломана"):
        run_pipeline(_context(tmp_path, count=1), (_source_layer(),), producers=producers)


def test_check_error_is_wrapped_into_generator_error(tmp_path: Path) -> None:
    source = _FakeProducer(ArtifactKind.VIDEO)
    producers = ProducerRegistry({"video": lambda: source})
    checks = CheckRegistry({"broken": _RaisingCheck})
    layers = (_source_layer(checks=("broken",)),)

    with pytest.raises(GeneratorError, match="проверка сломана"):
        run_pipeline(_context(tmp_path, count=1), layers, producers=producers, checks=checks)


def test_plan_error_is_wrapped_into_generator_error(tmp_path: Path) -> None:
    producers = ProducerRegistry({"video": _RaisingProducer})

    with pytest.raises(GeneratorError, match="план сломан"):
        run_pipeline(
            _context(tmp_path, count=1, dry_run=True),
            (_source_layer(),),
            producers=producers,
        )


def test_producer_generator_error_is_not_wrapped(tmp_path: Path) -> None:
    producers = ProducerRegistry({"video": _GeneratorErrorProducer})

    with pytest.raises(GeneratorError, match="своя ошибка генерации"):
        run_pipeline(_context(tmp_path, count=1), (_source_layer(),), producers=producers)


def test_plan_generator_error_is_not_wrapped(tmp_path: Path) -> None:
    producers = ProducerRegistry({"video": _GeneratorErrorProducer})

    with pytest.raises(GeneratorError, match="своя ошибка плана"):
        run_pipeline(
            _context(tmp_path, count=1, dry_run=True),
            (_source_layer(),),
            producers=producers,
        )


def test_check_generator_error_is_not_wrapped(tmp_path: Path) -> None:
    source = _FakeProducer(ArtifactKind.VIDEO)
    producers = ProducerRegistry({"video": lambda: source})
    checks = CheckRegistry({"domain": _GeneratorErrorCheck})
    layers = (_source_layer(checks=("domain",)),)

    with pytest.raises(GeneratorError, match="своя ошибка проверки"):
        run_pipeline(_context(tmp_path, count=1), layers, producers=producers, checks=checks)


def test_producer_registry_registers_replaces_and_resets() -> None:
    registry = ProducerRegistry()
    first = _FakeProducer(ArtifactKind.VIDEO)
    second = _FakeProducer(ArtifactKind.TEXT)
    registry.register("layer", lambda: first)

    assert registry.available() == ("layer",)
    assert registry.resolve("layer") is first

    registry.register("layer", lambda: second)

    assert registry.resolve("layer") is second

    registry.reset()

    assert registry.available() == ()

    with pytest.raises(ProfileError, match="пустое имя producer"):
        registry.register(" ", lambda: first)

    with pytest.raises(ProfileError, match="неизвестный producer"):
        registry.resolve("absent")


def test_check_registry_registers_replaces_and_resets() -> None:
    registry = CheckRegistry()
    first = _FakeCheck("first")
    second = _FakeCheck("second")
    registry.register("mp4", lambda: first)

    assert registry.available() == ("mp4",)
    assert registry.resolve("mp4") is first

    registry.register("mp4", lambda: second)

    assert registry.resolve("mp4") is second

    registry.reset()

    assert registry.available() == ()

    with pytest.raises(ProfileError, match="пустое имя проверки"):
        registry.register(" ", lambda: first)

    with pytest.raises(ProfileError, match="неизвестная проверка"):
        registry.resolve("absent")
