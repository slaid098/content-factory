"""Public ports of production: the old producer port and the layer ports.

The old ``Producer`` and ``ProducedBatch`` stay until the layered pipeline
replaces them. The layer ports describe one step of that pipeline: a producer
that turns the artifacts of its input layers into new artifacts and a check
that accepts or rejects one artifact.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from content_factory.schemas.generator.pipeline import Artifact, ArtifactKind, CheckResult, Layer
from content_factory.schemas.generator.run import ProducedBatch, Producer, ProfileRef

if TYPE_CHECKING:
    from content_factory.config.settings import GeneratorSettings


@dataclass(frozen=True, slots=True)
class PipelineContext:
    """Inputs shared by every layer of one pipeline run."""

    profiles: tuple[ProfileRef, ...]
    count: int
    data_dir: Path
    settings: GeneratorSettings
    run_dir: Path | None = None
    dry_run: bool = False
    rng: random.Random = field(default_factory=random.Random)
    now: datetime = field(default_factory=lambda: datetime.now(UTC))


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


__all__ = [
    "LayerCheck",
    "LayerProducer",
    "PipelineContext",
    "ProducedBatch",
    "Producer",
    "ProfileRef",
]
