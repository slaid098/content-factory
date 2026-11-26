"""HTTP response bodies of the stock endpoint."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LanguageStockResponse(BaseModel):
    """Stock position of one language in the API response."""

    model_config = ConfigDict(frozen=True)

    language: str
    stock: int
    target: int
    deficit: int
    titled: int = Field(ge=0)
    untitled: int = Field(ge=0)
    video_bytes: int = Field(ge=0)


class GeneralStockResponse(BaseModel):
    """General video stock of one language in the API response."""

    model_config = ConfigDict(frozen=True)

    language: str
    stock: int = Field(ge=0)
    target: int


class StockResponse(BaseModel):
    """Stock, loop state and snapshot time returned by the stock endpoint."""

    model_config = ConfigDict(frozen=True)

    generated_at: datetime
    last_production_at: datetime | None
    captcha_blocked_until: datetime | None
    captcha_streak: int
    zero_runs: int
    languages: tuple[LanguageStockResponse, ...]
    general: tuple[GeneralStockResponse, ...]
