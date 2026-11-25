"""Typed models of a generator run and of a profile production recipe."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel, ConfigDict, Field

from content_factory.schemas.content.delivery import ContentType

if TYPE_CHECKING:
    from content_factory.config.settings import GeneratorSettings


@dataclass(frozen=True, slots=True)
class ProductionStep:
    """One way to produce content: producer, media type and QA checks."""

    producer: str
    type: ContentType = ContentType.VIDEO
    qa: tuple[str, ...] = ("media", "clef")


@dataclass(frozen=True, slots=True)
class ProducedBatch:
    """Outcome of one production step."""

    run_id: str
    generated: int
    failed: int
    run_dirs: tuple[Path, ...]


class ProfileRef(Protocol):
    """Profile fields a producer may read."""

    @property
    def name(self) -> str:
        """Profile name such as ``dating/profile_2``."""
        ...


class Producer(Protocol):
    """Named way to produce content of one profile."""

    @property
    def supported_types(self) -> frozenset[ContentType]:
        """Content types this producer can make."""
        ...

    def produce(
        self,
        profile: ProfileRef,
        count: int,
        data_dir: Path,
        settings: GeneratorSettings,
    ) -> ProducedBatch:
        """Produce up to *count* items and return the batch outcome."""
        ...


class RunStatus(StrEnum):
    """Status of a batch run and of one of its items."""

    RUNNING = "running"
    GENERATING = "generating"
    POSTPROCESSING = "postprocessing"
    DONE = "done"
    FAILED = "failed"
    DRY_RUN = "dry_run"


class RunServer(BaseModel):
    """Vast.ai server rented for the batch; empty in a dry run."""

    model_config = ConfigDict(extra="forbid")

    contract_id: str | None = None
    ip: str | None = None
    gpu: str | None = None
    image: str | None = None
    cost_per_hour: float | None = None


class RunStep(BaseModel):
    """One production step of the profile recipe."""

    model_config = ConfigDict(extra="forbid")

    producer: str
    type: ContentType = ContentType.VIDEO
    qa: list[str] = Field(default_factory=list)


class RunItem(BaseModel):
    """One planned item: its video look and length or photo post file."""

    model_config = ConfigDict(extra="forbid")

    file: str
    profile: str
    look_id: str | None = None
    seed: int | None = None
    frames: int | None = None
    duration: float | None = None
    status: RunStatus = RunStatus.RUNNING
    error: str | None = None
    producer: str | None = None
    type: ContentType = ContentType.VIDEO
    qa: list[str] = Field(default_factory=list)


class RunTotals(BaseModel):
    """Counters of the batch: produced, failed and produced bytes."""

    model_config = ConfigDict(extra="forbid")

    generated: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    bytes: int = Field(default=0, ge=0)


class RunReport(BaseModel):
    """Contents of ``run.json``, written to every batch directory."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    started_at: datetime
    finished_at: datetime | None = None
    status: RunStatus = RunStatus.RUNNING
    profiles: list[str] = Field(default_factory=list)
    count: int = 0
    production: list[RunStep] = Field(default_factory=list)
    server: RunServer | None = None
    items: list[RunItem] = Field(default_factory=list)
    totals: RunTotals = Field(default_factory=RunTotals)
