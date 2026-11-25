"""Types of a layered production pipeline.

A pipeline is a sequence of layers. Every layer produces artifacts from the
artifacts of the layers it declares as inputs, and its checks accept or reject
each artifact before the next layer runs. These types are shared by the
pipeline engine, the layer ports and the producers and checks of a recipe.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from content_factory.schemas.content.delivery import ContentType
from content_factory.schemas.generator.run import ProfileRef
from content_factory.utils.run_id import validate_run_id

if TYPE_CHECKING:
    from content_factory.config.settings import GeneratorSettings


class ArtifactKind(StrEnum):
    """Kind of content a layer produces and the next layer consumes."""

    VIDEO = "video"
    TEXT = "text"
    PHOTO = "photo"


def artifact_kind(content_type: ContentType) -> ArtifactKind:
    """Return the artifact kind of a profile content type.

    Raises:
        ValueError: The content type has no artifact kind.
    """
    return ArtifactKind(content_type.value)


def content_type(kind: ArtifactKind) -> ContentType:
    """Return the profile content type of an artifact kind.

    Raises:
        ValueError: The kind is not a content type produced by a profile.
    """
    return ContentType(kind.value)


@dataclass(frozen=True, slots=True)
class Artifact:
    """One produced file of a layer.

    ``item`` and ``profile`` identify the item across layers: a later layer
    must keep both when it transforms an artifact it received as an input.
    """

    item: str
    profile: str
    kind: ArtifactKind
    path: Path
    media_type: str | None
    meta: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Layer:
    """One pipeline step: a producer, its declared inputs and its checks."""

    name: str
    producer: str
    inputs: tuple[str, ...]
    output: ArtifactKind
    checks: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Verdict of one layer check on one artifact."""

    passed: bool
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ItemFailure:
    """One item that did not reach the end of the pipeline."""

    item: str
    profile: str
    layer: str
    reason: str


@dataclass(frozen=True, slots=True)
class LayerOutcome:
    """Artifacts a layer produced and the failures it recorded.

    ``artifacts`` are the ones accepted by the layer checks; ``produced`` is
    everything the producer returned, including rejected items, so a report
    can show failed items with their planned identity.
    """

    layer: str
    artifacts: tuple[Artifact, ...]
    failures: tuple[ItemFailure, ...]
    produced: tuple[Artifact, ...] = ()


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Outcome of every layer and the artifacts left at the end."""

    layers: tuple[LayerOutcome, ...]
    artifacts: tuple[Artifact, ...]
    failures: tuple[ItemFailure, ...]


class RecipeProfile(ProfileRef, Protocol):
    """Profile fields recipe grouping and pipeline orchestration read."""

    @property
    def language(self) -> str:
        """Content language of the profile."""
        ...

    @property
    def production(self) -> tuple[Layer, ...]:
        """Independent layer chains of the profile recipe."""
        ...


@dataclass(frozen=True, slots=True)
class PipelineContext:
    """Inputs shared by every layer of one pipeline run."""

    profiles: tuple[ProfileRef, ...]
    count: int
    data_dir: Path
    settings: GeneratorSettings
    run_id: str
    run_dir: Path | None = None
    dry_run: bool = False
    rng: random.Random = field(default_factory=random.Random)
    now: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        """Reject a run id that cannot name a directory of this run.

        Raises:
            GeneratorError: The id is not a generated timestamp id.
        """
        validate_run_id(self.run_id)


class LayerProducer(Protocol):
    """Port of one pipeline layer that makes artifacts from its inputs."""

    @property
    def supported_outputs(self) -> frozenset[ArtifactKind]:
        """Artifact kinds this producer can make."""
        ...

    def plan_layer(
        self,
        layer: Layer,
        context: PipelineContext,
    ) -> tuple[Artifact, ...]:
        """Describe the artifacts of *layer* without generating anything."""
        ...

    def produce_layer(
        self,
        layer: Layer,
        inputs: tuple[Artifact, ...],
        context: PipelineContext,
    ) -> tuple[Artifact, ...]:
        """Make the artifacts of *layer* from *inputs*."""
        ...


class LayerCheck(Protocol):
    """Port of one named check of a pipeline layer."""

    def check(
        self,
        layer: Layer,
        artifact: Artifact,
        context: PipelineContext,
    ) -> CheckResult:
        """Return whether *artifact* may pass to the next layer."""
        ...
