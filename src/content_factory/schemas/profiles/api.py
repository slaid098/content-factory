"""HTTP bodies of the profile endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from content_factory.schemas.generator.run import RunStatus


class ProfileLastRun(BaseModel):
    """Outcome of the newest batch of one profile."""

    model_config = ConfigDict(frozen=True)

    status: RunStatus
    finished_at: datetime | None
    generated: int = Field(ge=0)
    failed: int = Field(ge=0)
    error: str | None = None


class ProfileResponse(BaseModel):
    """One generator profile with its video and photo stock and refill settings."""

    model_config = ConfigDict(frozen=True)

    id: str
    category: str
    name: str
    language: str
    niche: str
    title: str | None = None
    summary: str | None = None
    width: int
    height: int
    audio: bool
    outputs: tuple[Literal["video", "photo"], ...]
    stock: int = Field(ge=0)
    photo_stock: int = Field(ge=0)
    target: int
    refill_at: int
    refill_enabled: bool
    target_override: int | None
    refill_at_override: int | None
    photo_target: int = Field(ge=0)
    photo_refill_at: int = Field(ge=0)
    preview_url: str
    last_run: ProfileLastRun | None


class ProfilesResponse(BaseModel):
    """Every readable profile of the registry."""

    model_config = ConfigDict(frozen=True)

    profiles: tuple[ProfileResponse, ...]


class ProductionCheck(BaseModel):
    """One QA check of a production step and the stage that runs it."""

    model_config = ConfigDict(frozen=True)

    name: str
    stage: Literal["source", "final"]


class ProductionStep(BaseModel):
    """One step of the profile production recipe in declaration order."""

    model_config = ConfigDict(frozen=True)

    producer: str
    type: Literal["video", "photo"]
    checks: tuple[ProductionCheck, ...]


class ProfileOverlayStyle(BaseModel):
    """Every text card geometry and colour field of a profile."""

    model_config = ConfigDict(frozen=True)

    font_size: float
    block1_y: float
    block2_y: float | None
    gap: float
    hug_text: bool
    max_text_width: float
    card_width: float | None
    text_pad_x: float
    cta_scale: float
    cta_divider: bool
    merge_blocks: bool
    text_color: str
    text_bg_color: str
    text_bg_alpha: int
    block2_bg: str | None
    block2_text: str | None
    block2_alpha: int | None


class ProfileVideoSettings(BaseModel):
    """Read-only video parameters of one profile."""

    model_config = ConfigDict(frozen=True)

    width: int
    height: int
    frames: int
    frames_jitter: int
    fps: int
    renderer: str
    image: str
    audio_tracks: int


class ProfileRentSettings(BaseModel):
    """Read-only server rent filters of one profile."""

    model_config = ConfigDict(frozen=True)

    gpu: str
    max_cost: float
    disk_space: int
    gpu_ram: int
    inet_down: int
    inet_up_cost: float
    inet_down_cost: float


class ProfileTextSettings(BaseModel):
    """Read-only text card parameters; the font is a file name, not a path."""

    model_config = ConfigDict(frozen=True)

    font: str
    negative_prompt: str | None
    cfg: float | None
    style: ProfileOverlayStyle


class ProfileDeliverySettings(BaseModel):
    """Read-only delivery policy of one profile."""

    model_config = ConfigDict(frozen=True)

    title_mode: Literal["generated", "fixed"]
    title: str | None
    hashtags: tuple[str, ...]


class ProfileSettings(BaseModel):
    """Safe read-only settings groups of one profile."""

    model_config = ConfigDict(frozen=True)

    video: ProfileVideoSettings
    rent: ProfileRentSettings
    text: ProfileTextSettings
    delivery: ProfileDeliverySettings


class ProfileDetailResponse(ProfileResponse):
    """One profile with its recipe and safe settings."""

    production: tuple[ProductionStep, ...]
    settings: ProfileSettings


class PromptFile(BaseModel):
    """Text of one prompt file with the name the profile uses."""

    model_config = ConfigDict(frozen=True)

    filename: str
    text: str


class PromptCard(BaseModel):
    """One appearance card with its stable prompt and override flag."""

    model_config = ConfigDict(frozen=True)

    id: str
    values: dict[str, object]
    prompt: str
    overridden: bool


class ProfilePrompts(BaseModel):
    """Prompt files and cards of one profile."""

    model_config = ConfigDict(frozen=True)

    template: PromptFile
    cards: tuple[PromptCard, ...]
    photo: PromptFile | None


class PromptTextRequest(BaseModel):
    """New text of a template, a card override or a photo prompt."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: str


class PromptCardResponse(BaseModel):
    """Rebuilt prompt of one card after an override change."""

    model_config = ConfigDict(frozen=True)

    id: str
    prompt: str
    overridden: bool


class ProfileUpdateRequest(BaseModel):
    """Partial update of the per-profile refill settings.

    A missing field keeps its value; an explicit ``null`` clears the
    override of ``target`` or ``refill_at`` and falls back to the global
    generator value. The photo goal and threshold are stored as concrete
    values without a global fallback, so an explicit ``null`` is rejected
    there. ``photo_target = 0`` turns photo refill off and requires
    ``photo_refill_at = 0``; otherwise the threshold stays below the goal.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    refill_enabled: bool | None = None
    target: int | None = Field(default=None, ge=1, le=1000)
    refill_at: int | None = Field(default=None, ge=0)
    photo_target: int | None = Field(default=None, ge=0, le=1000)
    photo_refill_at: int | None = Field(default=None, ge=0, le=999)

    @model_validator(mode="after")
    def _check_explicit_values(self) -> ProfileUpdateRequest:
        """Reject an explicit ``null`` for fields that always hold a value."""
        if "refill_enabled" in self.model_fields_set and self.refill_enabled is None:
            raise ValueError("refill_enabled должен быть true или false")
        for name in ("photo_target", "photo_refill_at"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} должен быть числом")
        return self


__all__ = [
    "ProductionCheck",
    "ProductionStep",
    "ProfileDeliverySettings",
    "ProfileDetailResponse",
    "ProfileLastRun",
    "ProfileOverlayStyle",
    "ProfilePrompts",
    "ProfileRentSettings",
    "ProfileResponse",
    "ProfileSettings",
    "ProfileTextSettings",
    "ProfileUpdateRequest",
    "ProfileVideoSettings",
    "ProfilesResponse",
    "PromptCard",
    "PromptCardResponse",
    "PromptFile",
    "PromptTextRequest",
]
