"""Metadata of one downloaded video, shared by delivery and title generation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class VideoMeta:
    """Description, search topic and duration of one downloaded video."""

    description: str
    query: str
    duration: int | None = None
