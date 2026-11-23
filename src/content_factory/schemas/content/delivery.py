"""Request body and job state of the content delivery endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from content_factory.constants import LANGUAGE_CODE_RE

DeliveryStatus = Literal["building", "ready", "ready_local", "empty", "failed"]


class DeliveryRequest(BaseModel):
    """One request for a language share of the stock."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    language: str = Field(pattern=LANGUAGE_CODE_RE.pattern)
    count: int = Field(ge=1, le=300)
    request_id: UUID
    query: str | None = Field(default=None, min_length=1, max_length=200)


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
