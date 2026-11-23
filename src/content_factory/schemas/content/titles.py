"""Title generation result types."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TitlePassResult:
    """Outcome of one title assignment pass."""

    candidates: int
    titled: int
    skipped: int
