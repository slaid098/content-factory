"""Request body and job state of the content delivery endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from content_factory.constants import LANGUAGE_CODE_RE

DeliveryStatus = Literal["building", "ready", "ready_local", "empty", "failed"]


class DeliveryRequest(BaseModel):
    """One request for a language or a single generator profile.

    Exactly one of ``language`` and ``profile`` selects the stock. A query
    topic narrows a language share only; a profile is already unambiguous.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    language: str | None = Field(default=None, pattern=LANGUAGE_CODE_RE.pattern)
    profile: str | None = Field(default=None, min_length=1, max_length=200)
    count: int = Field(ge=1, le=300)
    request_id: UUID
    query: str | None = Field(default=None, min_length=1, max_length=200)

    @model_validator(mode="after")
    def _check_target(self) -> DeliveryRequest:
        """Require exactly one stock selector and reject a profile query."""
        if (self.language is None) == (self.profile is None):
            raise ValueError("укажите ровно одно из language или profile")
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
