"""One generator profile as the registry sees it."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from content_factory.schemas.content.delivery import ContentType
from content_factory.schemas.profiles.delivery import DeliveryInfo

DEFAULT_OUTPUTS = frozenset({ContentType.VIDEO})
"""Content types of a profile that has no ``production`` block."""


@dataclass(frozen=True, slots=True)
class ProfileInfo:
    """Profile fields needed to plan production and count its stock.

    The registry reads only ``profile.yaml``; prompts, fonts and appearance
    cards are validated later by the generator itself. ``outputs`` holds the
    content types of the ``production`` block, so refill never asks a profile
    to produce a type it has no layer for; a profile without the block keeps
    the legacy video recipe.
    """

    id: str
    category: str
    name: str
    language: str
    niche: str
    width: int
    height: int
    audio: bool
    dir: Path
    delivery: DeliveryInfo = field(default_factory=DeliveryInfo)
    title: str | None = None
    summary: str | None = None
    outputs: frozenset[ContentType] = DEFAULT_OUTPUTS


__all__ = ["DEFAULT_OUTPUTS", "ProfileInfo"]
