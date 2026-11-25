"""Fixed title policy: one profile title for every video."""

from __future__ import annotations

from collections.abc import Mapping


class FixedTitle:
    """Use the single title of the profile for every video."""

    needs_generation = False

    def __init__(self, value: str) -> None:
        self._value = value

    def title_for(self, sha256: str, stored: Mapping[str, str]) -> str | None:
        """Return the profile title, ignoring the stored one."""
        del sha256, stored
        return self._value
