"""Delivery sidecar policy of one generator profile."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class TitleMode(StrEnum):
    """How the video titles of one profile are chosen."""

    GENERATED = "generated"
    FIXED = "fixed"


@dataclass(frozen=True, slots=True)
class TitleConfig:
    """Title mode of a profile and, for ``fixed``, its single title."""

    mode: TitleMode = TitleMode.GENERATED
    value: str | None = None


@dataclass(frozen=True, slots=True)
class DeliveryInfo:
    """Title policy and hashtags written into the sidecar of a profile."""

    title: TitleConfig = field(default_factory=TitleConfig)
    hashtags: tuple[str, ...] = ()


class TitlePolicy(Protocol):
    """Whether a profile needs generated titles and the title of one file."""

    needs_generation: bool

    def title_for(self, sha256: str, stored: Mapping[str, str]) -> str | None:
        """Return the video title, or ``None`` when the file has none."""
        ...


__all__ = ["DeliveryInfo", "TitleConfig", "TitleMode", "TitlePolicy"]
