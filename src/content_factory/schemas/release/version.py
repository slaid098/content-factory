"""Release identity returned by the version endpoint."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class VersionResponse(BaseModel):
    """Build identity of the running API image."""

    model_config = ConfigDict(frozen=True)

    version: str = Field(min_length=1)
    built_at: datetime | None
