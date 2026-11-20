"""Unified diff for the runtime settings file."""

from __future__ import annotations

import difflib


def unified_diff(old: str, new: str) -> str:
    """Return a unified diff with stable ``config.yaml`` file names."""
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile="config.yaml",
            tofile="config.yaml",
        )
    )
