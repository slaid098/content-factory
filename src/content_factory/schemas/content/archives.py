"""HTTP response body of the delivery archive list."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ArchiveInfo(BaseModel):
    """One live delivery archive."""

    model_config = ConfigDict(frozen=True)

    language: str
    request_id: UUID
    size: int
    created_at: datetime
    expires_at: datetime
