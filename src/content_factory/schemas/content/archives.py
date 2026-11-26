"""HTTP response body of the delivery archive list."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ArchiveInfo(BaseModel):
    """One live delivery archive, stored locally or on Filebin."""

    model_config = ConfigDict(frozen=True)

    language: str
    request_id: UUID
    size: int
    created_at: datetime
    expires_at: datetime
    storage: Literal["local", "remote"] = "local"
    url: str | None = None
    files: int | None = None
    profile: str | None = None
