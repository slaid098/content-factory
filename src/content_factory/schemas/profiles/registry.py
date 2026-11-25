"""One generator profile as the registry sees it."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from content_factory.schemas.profiles.delivery import DeliveryInfo


@dataclass(frozen=True, slots=True)
class ProfileInfo:
    """Profile fields needed to plan production and count its stock.

    The registry reads only ``profile.yaml``; prompts, fonts and appearance
    cards are validated later by the generator itself.
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


__all__ = ["ProfileInfo"]
