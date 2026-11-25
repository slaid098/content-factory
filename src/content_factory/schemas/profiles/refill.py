"""Refill settings of a generator profile and the decision they produce."""

from __future__ import annotations

from dataclasses import dataclass

from content_factory.schemas.content.delivery import ContentType


@dataclass(frozen=True, slots=True)
class ProfileRefill:
    """Per-profile overrides of the global refill settings.

    ``None`` in ``target`` or ``refill_at`` means the global configuration
    value; ``refill_at`` may be overridden to zero, disabling a refill only
    while stock is empty.

    ``photo_target`` and ``photo_refill_at`` are a separate pair for photo
    posts and have no global fallback: ``photo_target = 0`` turns photo refill
    off, so the default row changes neither video refill nor the current
    behaviour.
    """

    enabled: bool
    target: int | None
    refill_at: int | None
    photo_target: int = 0
    photo_refill_at: int = 0


@dataclass(frozen=True, slots=True)
class RefillDecision:
    """One profile and content type that need a refill and how many to add."""

    profile_id: str
    count: int
    target: int
    refill_at: int
    content_type: ContentType


__all__ = ["ProfileRefill", "RefillDecision"]
