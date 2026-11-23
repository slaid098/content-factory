"""Typed ``run.json`` report of one generator batch."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


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


class RunItem(BaseModel):
    """One planned video: its look, seed, length and final file."""

    model_config = ConfigDict(extra="forbid")

    file: str
    profile: str
    look_id: str
    seed: int
    frames: int
    duration: float | None = None
    status: RunStatus = RunStatus.RUNNING
    error: str | None = None


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
    server: RunServer | None = None
    items: list[RunItem] = Field(default_factory=list)
    totals: RunTotals = Field(default_factory=RunTotals)
