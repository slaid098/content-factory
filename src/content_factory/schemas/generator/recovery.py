"""Counters of one orphaned-server recovery pass."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RecoveryOutcome:
    """Counters of one recovery pass."""

    destroyed: int = 0
    lost: int = 0
    failed: int = 0


__all__ = ["RecoveryOutcome"]
