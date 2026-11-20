"""Stock snapshots shared by the flow and the API."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class LanguageStock:
    """Stock position of one language at snapshot time."""

    language: str
    stock: int
    target: int
    deficit: int
    titled: int
    untitled: int
    video_bytes: int


@dataclass(frozen=True, slots=True)
class StockSnapshot:
    """Stock position of every language at one moment."""

    generated_at: datetime
    languages: tuple[LanguageStock, ...]
