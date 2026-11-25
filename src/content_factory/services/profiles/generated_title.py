"""Generated title policy: the per-video title of the ``titles`` table."""

from __future__ import annotations

from collections.abc import Mapping


class GeneratedTitle:
    """Use the per-video title stored in ``titles``."""

    needs_generation = True

    def title_for(self, sha256: str, stored: Mapping[str, str]) -> str | None:
        """Return the stored title; whitespace-only counts as absent."""
        title = stored.get(sha256)
        if title is None or not title.strip():
            return None
        return title
