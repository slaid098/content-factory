"""Request body of the content delivery endpoint."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DeliveryRequest(BaseModel):
    """One request for a language share of the stock."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    language: str = Field(pattern=r"^[a-z]{2}$")
    count: int = Field(ge=1, le=10)
    request_id: UUID
    query: str | None = Field(default=None, min_length=1, max_length=200)
