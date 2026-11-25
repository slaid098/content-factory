"""Text model contract shared by the generator and profile description."""

from __future__ import annotations

from typing import Protocol

DEFAULT_USER_MESSAGE = "Сгенерируй один текст для одного видео."


class TextWriter(Protocol):
    """Port of the OpenAI-compatible text model."""

    def write(self, system_prompt: str, user_prompt: str = DEFAULT_USER_MESSAGE) -> str:
        """Return the raw answer of the model for one request."""
        ...


__all__ = ["DEFAULT_USER_MESSAGE", "TextWriter"]
