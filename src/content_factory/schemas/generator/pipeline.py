"""Types of a layered production pipeline.

A pipeline is a sequence of layers. Every layer produces artifacts from the
artifacts of the layers it declares as inputs, and its checks accept or reject
each artifact before the next layer runs. These types are shared by the
pipeline engine, the layer ports and the producers and checks of a recipe.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class ArtifactKind(StrEnum):
    """Kind of content a layer produces and the next layer consumes."""

    VIDEO = "video"
    TEXT = "text"
    PHOTO = "photo"


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
    """Artifacts a layer produced and the failures it recorded."""

    layer: str
    artifacts: tuple[Artifact, ...]
    failures: tuple[ItemFailure, ...]


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Outcome of every layer and the artifacts left at the end."""

    layers: tuple[LayerOutcome, ...]
    artifacts: tuple[Artifact, ...]
    failures: tuple[ItemFailure, ...]
