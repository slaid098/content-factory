"""Authorization header shared by the generator's model clients."""

from __future__ import annotations

from content_factory.exceptions.generator import GeneratorError

_PRINTABLE_KEY_RANGE = range(33, 127)


def bearer_header(key: str, error: type[GeneratorError], subject: str) -> str:
    """Build the ``Bearer`` header value of one API key.

    The value must be printable ASCII without whitespace; other characters
    make httpx raise an error that embeds the raw header.

    Args:
        key: Raw API key.
        error: Capsule error raised for a malformed key.
        subject: Human-readable key name for the error message.

    Returns:
        The ``Bearer <key>`` header value.

    Raises:
        GeneratorError: The key has a character outside printable ASCII.
    """
    if any(ord(char) not in _PRINTABLE_KEY_RANGE for char in key):
        raise error(f"ключ {subject} содержит недопустимые символы")
    return f"Bearer {key}"
