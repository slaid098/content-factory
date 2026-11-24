"""Refill settings of a generator profile and the decision they produce."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProfileRefill:
    """Per-profile overrides of the global refill settings.

    ``None`` in ``target`` or ``refill_at`` means the global configuration
    value; ``refill_at`` may be overridden to zero, disabling a refill only
    while stock is empty.
    """

    enabled: bool
    target: int | None
    refill_at: int | None


@dataclass(frozen=True, slots=True)
class RefillDecision:
    """One profile that needs a refill and how many videos to add."""

    profile_id: str
    count: int
    target: int
    refill_at: int


__all__ = ["ProfileRefill", "RefillDecision"]
