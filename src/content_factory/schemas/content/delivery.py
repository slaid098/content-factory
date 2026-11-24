"""Request body and job state of the content delivery endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from content_factory.constants import LANGUAGE_CODE_RE

DeliveryStatus = Literal["building", "ready", "ready_local", "empty", "failed"]

GENERAL_PROFILE = "general"


class DeliveryRequest(BaseModel):
    """One delivery address: a language, optionally narrowed to a profile.

    ``language`` is required and always selects the stock. A ``profile`` such
    as ``dating/profile_1`` adds the rows of that profile of the same language;
    the explicit profile ``general`` means no profile. A ``query`` topic
    belongs to supplier content and is allowed only without a profile.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    language: str = Field(pattern=LANGUAGE_CODE_RE.pattern)
    profile: str | None = Field(default=None, min_length=1, max_length=200)
    count: int = Field(ge=1, le=300)
    request_id: UUID
    query: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("profile", mode="before")
    @classmethod
    def _general_is_no_profile(cls, value: object) -> object:
        """Treat the explicit ``general`` profile as the General mode."""
        return None if value == GENERAL_PROFILE else value

    @model_validator(mode="after")
    def _check_query(self) -> DeliveryRequest:
        """Reject a query topic together with a profile."""
        if self.profile is not None and self.query is not None:
            raise ValueError("query нельзя сочетать с profile")
        return self


class DeliveryJobInfo(BaseModel):
    """State of one asynchronous temporary-link delivery job."""

    model_config = ConfigDict(frozen=True)

    request_id: UUID
    language: str
    status: DeliveryStatus
    created_at: datetime
    expires_at: datetime
    size: int = 0
    files: int = 0
    url: str | None = None
    error: str | None = None
