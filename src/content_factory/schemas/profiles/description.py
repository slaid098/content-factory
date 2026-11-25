"""Generated human-readable description of one profile."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProfileDescription:
    """Generated title and summary of one profile."""

    title: str
    summary: str


__all__ = ["ProfileDescription"]
