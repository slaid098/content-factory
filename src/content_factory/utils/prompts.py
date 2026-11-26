"""Prompt files and card overrides shared by profiles and the generator.

The generator reads the scene template, the text prompt and the card
overrides through this module, while the profile API reads and writes them
here as well. The module sits in the cross-cutting ``utils`` layer, so both
capsules use it without importing each other.
"""

from __future__ import annotations

import os
import random
import re
import stat
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

import yaml

from content_factory.exceptions.errors import ProfileStorageError, ProfileValidationError
from content_factory.exceptions.generator import ProfileError

TEMPLATE_FILENAMES = ("prompt_template.txt", "video_prompt.txt")
TEXT_PROMPT_FILENAME = "text_prompt.txt"
OVERRIDES_FILENAME = "look_overrides.yaml"
MAX_PROMPT_LENGTH = 20_000
_SPIN = re.compile(r"\{([^{}|]+(?:\|[^{}]+)+)\}")


def template_path(directory: Path) -> Path:
    """Return the scene template actually used by a profile.

    Args:
        directory: Profile directory.

    Returns:
        ``prompt_template.txt`` or, for older profiles, ``video_prompt.txt``.

    Raises:
        ProfileError: Neither file exists.
    """
    for name in TEMPLATE_FILENAMES:
        path = directory / name
        if path.is_file():
            return path
    raise ProfileError(f"нет prompt_template.txt или video_prompt.txt: {directory}")


def read_template_file(directory: Path) -> tuple[str, str]:
    """Read the used scene template with its file name.

    Args:
        directory: Profile directory.

    Returns:
        Name and text of the template file.

    Raises:
        ProfileError: The file is missing, unreadable or empty.
    """
    path = template_path(directory)
    text = _read_text(path)
    if not text.strip():
        raise ProfileError(f"пустой промпт {path}")
    return path.name, text


def read_text_prompt_file(directory: Path) -> tuple[str, str]:
    """Read ``text_prompt.txt`` with its file name.

    Args:
        directory: Profile directory.

    Returns:
        Name and text of the text prompt.

    Raises:
        ProfileError: The file is missing, unreadable or empty.
    """
    path = directory / TEXT_PROMPT_FILENAME
    if not path.is_file():
        raise ProfileError(f"нет text_prompt.txt: {path}")
    text = _read_text(path)
    if not text.strip():
        raise ProfileError(f"пустой text_prompt.txt: {path}")
    return path.name, text


def read_look_overrides(directory: Path) -> dict[str, str]:
    """Read card prompt overrides from ``look_overrides.yaml``.

    Args:
        directory: Profile directory.

    Returns:
        Card id to prompt text; an absent file yields an empty mapping.

    Raises:
        ProfileError: The file is unreadable, malformed or holds a blank
            value or a non-string value.
    """
    path = directory / OVERRIDES_FILENAME
    if not path.is_file():
        return {}
    try:
        payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as ex:
        raise ProfileError(f"не удалось прочитать overrides {path}: {ex}") from ex
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise ProfileError(f"look_overrides.yaml должен быть словарём: {path}")
    raw_prompts = payload.get("prompts")
    if raw_prompts is None:
        return {}
    if not isinstance(raw_prompts, Mapping):
        raise ProfileError(f"overrides должны быть словарём: {path}")
    overrides: dict[str, str] = {}
    for raw_id, raw_text in raw_prompts.items():
        look_id = str(raw_id).strip()
        if not look_id:
            raise ProfileError(f"пустой id карточки в {path}")
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise ProfileError(f"override карточки {look_id} должен быть непустой строкой")
        overrides[look_id] = raw_text
    return overrides


def write_look_overrides(directory: Path, overrides: Mapping[str, str]) -> None:
    """Replace ``look_overrides.yaml`` atomically with the given overrides.

    Args:
        directory: Profile directory.
        overrides: Card id to prompt text; an empty mapping clears every
            override but keeps the file.

    Raises:
        ProfileStorageError: The file cannot be written.
    """
    payload = {"prompts": dict(sorted(overrides.items()))}
    text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=True)
    write_text_file(directory / OVERRIDES_FILENAME, text)


def write_text_file(path: Path, text: str) -> None:
    """Replace a prompt file atomically, keeping the old file mode.

    The temporary file lives next to the target, so ``Path.replace`` stays
    atomic on one filesystem. A failed write leaves the old file intact and
    removes the temporary file.

    Args:
        path: Target file.
        text: Complete new content.

    Raises:
        ProfileStorageError: The file cannot be written.
    """
    temporary: Path | None = None
    try:
        mode = stat.S_IMODE(path.stat().st_mode) if path.is_file() else 0o644
        descriptor, name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        temporary.replace(path)
        temporary = None
    except OSError as ex:
        raise ProfileStorageError(f"не удалось записать {path.name}: {ex}") from ex
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink()


def validate_prompt_text(text: str) -> str:
    """Validate a prompt body and return it unchanged.

    Args:
        text: Prompt text sent by a client.

    Returns:
        The original text, so surrounding whitespace is not lost.

    Raises:
        ProfileValidationError: The stripped text is empty or longer than
            :data:`MAX_PROMPT_LENGTH` characters.
    """
    clean = text.strip()
    if not clean:
        raise ProfileValidationError("промпт не может быть пустым")
    if len(clean) > MAX_PROMPT_LENGTH:
        raise ProfileValidationError(f"промпт длиннее {MAX_PROMPT_LENGTH} символов: {len(clean)}")
    return text


def age_text(age: object) -> str:
    """Return an age phrase; the model holds words better than bare numbers."""
    try:
        value = int(str(age))
    except (TypeError, ValueError):
        return ""
    if value < 30:
        return "in her late twenties"
    if value <= 31:
        return "around thirty"
    if value <= 33:
        return "in her early thirties"
    if value <= 35:
        return "in her mid-thirties"
    return "in her late thirties"


def render_prompt(
    template: str,
    values: Mapping[str, object],
    rng: random.Random,
) -> str:
    """Fill a scene template with card values and spin options.

    Card fields replace their ``{placeholders}`` before the ``{a|b}`` spin
    options are expanded.

    Args:
        template: Scene template text.
        values: Appearance card values.
        rng: Random source for spin options.

    Returns:
        Final scene prompt for the ComfyUI text node.
    """
    filled = dict(values)
    filled["age_text"] = age_text(filled.get("age"))
    text = template
    for key, value in filled.items():
        text = text.replace("{" + key + "}", str(value))
    return _SPIN.sub(lambda match: rng.choice(match.group(1).split("|")), text)


def card_prompt(
    template: str,
    values: Mapping[str, object],
    override: str | None,
    rng: random.Random,
) -> str:
    """Return the card override verbatim or the template filled with the card.

    Args:
        template: Scene template text.
        values: Appearance values of the card.
        override: Stored prompt of the card, or ``None``.
        rng: Random source for spin options.

    Returns:
        Final scene prompt for the ComfyUI text node.
    """
    if override is not None:
        return override
    return render_prompt(template, values, rng)


def _read_text(path: Path) -> str:
    """Read a prompt file as UTF-8 text."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as ex:
        raise ProfileError(f"не удалось прочитать {path}: {ex}") from ex


__all__ = [
    "MAX_PROMPT_LENGTH",
    "OVERRIDES_FILENAME",
    "TEMPLATE_FILENAMES",
    "TEXT_PROMPT_FILENAME",
    "age_text",
    "card_prompt",
    "read_look_overrides",
    "read_template_file",
    "read_text_prompt_file",
    "render_prompt",
    "template_path",
    "validate_prompt_text",
    "write_look_overrides",
    "write_text_file",
]
