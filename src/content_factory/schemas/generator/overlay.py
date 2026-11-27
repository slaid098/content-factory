"""Types of one overlay batch: prompts, plan, density and package.

An overlay is a silent clip of dense particles on a pure black background.
It is a production asset for the channel, not a stock item: these types never
reach the delivery API, the archive or the partner stock.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class DensityClass(StrEnum):
    """Quality class of one overlay by the share of bright pixels."""

    BRAK = "брак"
    WEAK = "слабо"
    NORMAL = "норма"
    DENSE = "плотно"


@dataclass(frozen=True, slots=True)
class Density:
    """Measured share of bright pixels and its quality class."""

    share: float
    kind: DensityClass


@dataclass(frozen=True, slots=True)
class PromptConflict:
    """One candidate prompt that is too close to an existing overlay prompt."""

    prompt: str
    other_profile: str
    other_line: int
    ratio: float
    blocking: bool


@dataclass(frozen=True, slots=True)
class OverlayPlanItem:
    """One planned clip: prompt, seed, length and deterministic file name."""

    seq: int
    filename: str
    prompt: str
    seed: int
    frames: int


@dataclass(frozen=True, slots=True)
class OverlayPlan:
    """Plan of one overlay profile before any server is rented."""

    profile: str
    run_id: str
    width: int
    height: int
    fps: int
    items: tuple[OverlayPlanItem, ...]
    seconds: float
    max_cost: float = 1.0


@dataclass(frozen=True, slots=True)
class OverlayPackage:
    """Packed archive and visual preview of the accepted clips."""

    directory: Path
    archive: Path
    preview: Path
    clips: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class OverlayRun:
    """Outcome of one overlay batch.

    ``densities`` maps the file name to the measured share, including clips
    rejected by QA. ``generated`` counts clips made by this run, ``skipped``
    names the clips that were already on disk and passed QA.
    """

    plan: OverlayPlan
    accepted: tuple[Path, ...]
    rejected: tuple[tuple[Path, str], ...]
    skipped: tuple[Path, ...]
    densities: dict[str, float]
    package: OverlayPackage | None = None
    dry_run: bool = False
