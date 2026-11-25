"""HTTP bodies of the profile endpoints."""

from __future__ import annotations

from datetime import datetime

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
    "ProfileLastRun",
    "ProfileResponse",
    "ProfileUpdateRequest",
    "ProfilesResponse",
]
