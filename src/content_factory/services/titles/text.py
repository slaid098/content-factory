"""Cleaning, normalization and validation of generated titles."""

from __future__ import annotations

import re
import unicodedata

_CYRILLIC_LANGUAGES = frozenset({"ru", "uk", "kk", "bg", "sr", "mk", "be"})
_FORBIDDEN_WORDS = frozenset({"видео", "video", "ролик", "shorts", "клип", "clip"})
_ALLOWED_PUNCTUATION = frozenset(".,!?—:-…")
_LINK = re.compile(
    r"(?:https?://|www\.|t\.me/|\b[a-z0-9-]{2,}\.[a-z]{2,}\b)",
    re.IGNORECASE,
)
_HASHTAG = re.compile(r"#\S+")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_TITLE_MIN_LENGTH = 15
_TITLE_MAX_LENGTH = 70
_TITLE_MIN_WORDS = 2
_TITLE_MAX_WORDS = 7


def clean_title(title: str) -> str:
    """Collapse whitespace and trim the edges of a title.

    Args:
        title: Raw title text.

    Returns:
        The title with single spaces between its words.
    """
    return " ".join(title.split())


def normalize_title_key(title: str) -> str:
    """Build the global uniqueness key of a title.

    The key ignores case, the ``ё``/``е`` difference, punctuation, emoji and
    underscores, so titles that differ only in formatting collide.

    Args:
        title: Title text.

    Returns:
        A non-empty lowercase key without punctuation.

    Raises:
        ValueError: The title has no letters or digits left.
    """
    folded = clean_title(title).casefold().replace("ё", "е")
    key = " ".join(
        "".join(char if char.isalnum() or char.isspace() else " " for char in folded).split()
    )
    if not key:
        raise ValueError("название не содержит букв и цифр")
    return key


def description_title_key(description: str) -> str:
    """Build the uniqueness key of the TikTok description.

    A generated title equal to that text repeats an existing title and must
    not be stored. Hashtags are removed and whitespace is collapsed before
    normalizing.

    Args:
        description: Raw ``videos.description`` value; may be empty.

    Returns:
        The normalized key, or ``""`` when the description has no words.
    """
    text = " ".join(_HASHTAG.sub(" ", clean_title(description)).split())
    if not text:
        return ""
    try:
        return normalize_title_key(text)
    except ValueError:
        return ""


def validate_title(title: str, language: str) -> bool:
    """Return whether a title may be stored for the given language.

    A valid title is 15–70 characters long, has 2–7 words and at least one
    letter. It contains no hashtags, mentions, links, quotes or emoji, no
    generic words such as ``видео``/``video``, and uses the script expected by
    the language: Cyrillic for ``ru``, ``uk``, ``kk``, ``bg``, ``sr``, ``mk``
    and ``be``, Latin for every other code.

    Args:
        title: Candidate title text.
        language: Two-letter language code of the content directory.

    Returns:
        ``True`` when the title passes every rule.
    """
    cleaned = clean_title(title)
    if not _TITLE_MIN_LENGTH <= len(cleaned) <= _TITLE_MAX_LENGTH:
        return False
    words = [token for token in cleaned.split() if any(char.isalnum() for char in token)]
    if not _TITLE_MIN_WORDS <= len(words) <= _TITLE_MAX_WORDS:
        return False
    if not any(char.isalpha() for char in cleaned):
        return False
    if not _allowed_characters(cleaned) or _LINK.search(cleaned):
        return False
    if _has_forbidden_word(cleaned):
        return False
    return _matches_language(cleaned, language)


def _allowed_characters(title: str) -> bool:
    """Return whether every character is a letter, digit, space or allowed mark."""
    return all(char.isalnum() or char == " " or char in _ALLOWED_PUNCTUATION for char in title)


def _has_forbidden_word(title: str) -> bool:
    """Return whether the title contains a generic word such as ``video``."""
    return any(token in _FORBIDDEN_WORDS for token in _WORD.findall(title.casefold()))


def _matches_language(title: str, language: str) -> bool:
    """Check the script requirements of the language."""
    has_cyrillic = any(_script(char) == "CYRILLIC" for char in title)
    has_latin = any(_script(char) == "LATIN" for char in title)
    if language in _CYRILLIC_LANGUAGES:
        return has_cyrillic
    return has_latin and not has_cyrillic


def _script(char: str) -> str | None:
    """Return the Unicode script name of a letter, or ``None``."""
    if not char.isalpha():
        return None
    name = unicodedata.name(char, "")
    return name.split(" ", 1)[0] if name else None
