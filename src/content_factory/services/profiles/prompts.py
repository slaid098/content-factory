"""Prompt API bodies of a profile: template, cards and photo text.

The file primitives live in :mod:`content_factory.utils.prompts` because the
generator uses them too. This module adds the profile API view: load the
validated profile, build every card prompt and store the overrides.
"""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from content_factory.constants import PROFILES_DIR
from content_factory.exceptions.generator import ProfileError
from content_factory.schemas.generator.pipeline import ArtifactKind, Layer
from content_factory.schemas.profiles.api import (
    ProfilePrompts,
    PromptCard,
    PromptCardResponse,
    PromptFile,
)
from content_factory.services.generator import load_profile
from content_factory.utils.prompts import (
    TEXT_PROMPT_FILENAME,
    card_prompt,
    read_look_overrides,
    read_template_file,
    read_text_prompt_file,
    template_path,
    validate_prompt_text,
    write_look_overrides,
    write_text_file,
)


class _PromptLook(Protocol):
    """Card fields prompt rendering reads."""

    @property
    def id(self) -> str:
        """Card id used by an override."""
        ...

    @property
    def values(self) -> Mapping[str, object]:
        """Appearance values of the card."""
        ...

    @property
    def prompt(self) -> str | None:
        """Override text, or ``None`` when the card uses the template."""
        ...


class _PromptProfile(Protocol):
    """Profile fields prompt rendering and the prompt view read."""

    @property
    def name(self) -> str:
        """Profile name for error messages."""
        ...

    @property
    def dir(self) -> Path:
        """Profile directory that holds the prompt files."""
        ...

    @property
    def looks(self) -> tuple[_PromptLook, ...]:
        """Appearance cards in ``looks.yaml`` order."""
        ...

    @property
    def production(self) -> tuple[Layer, ...]:
        """Validated production recipe of the profile."""
        ...


def read_profile_prompts(data_dir: Path, profile_id: str) -> ProfilePrompts:
    """Build the prompt view of one profile.

    Args:
        data_dir: Root of the runtime data directory, usually ``app_data``.
        profile_id: Profile identifier such as ``dating/profile_2``.

    Returns:
        The used template, every card with its stable prompt and the photo
        text when the recipe has a photo layer.

    Raises:
        ProfileError: The profile is missing or malformed.
    """
    profile = _load_profile(data_dir, profile_id)
    filename, template = read_template_file(profile.dir)
    cards = tuple(
        PromptCard(
            id=look.id,
            values=dict(look.values),
            prompt=_card_prompt(profile, look),
            overridden=look.prompt is not None,
        )
        for look in profile.looks
    )
    photo = None
    if _has_photo_layer(profile.production):
        name, text = read_text_prompt_file(profile.dir)
        photo = PromptFile(filename=name, text=text)
    return ProfilePrompts(
        template=PromptFile(filename=filename, text=template),
        cards=cards,
        photo=photo,
    )


def write_prompt_template(data_dir: Path, profile_id: str, text: str) -> PromptFile:
    """Replace the scene template used by one profile.

    Args:
        data_dir: Root of the runtime data directory, usually ``app_data``.
        profile_id: Profile identifier such as ``dating/profile_2``.
        text: New template text.

    Returns:
        The written file name and text.

    Raises:
        ProfileError: The profile is missing, malformed or has no template.
        ProfileValidationError: The text is blank or too long.
        ProfileStorageError: The file cannot be written.
    """
    profile = _load_profile(data_dir, profile_id)
    validate_prompt_text(text)
    path = template_path(profile.dir)
    write_text_file(path, text)
    return PromptFile(filename=path.name, text=text)


def write_card_prompt(
    data_dir: Path,
    profile_id: str,
    look_id: str,
    text: str,
) -> PromptCardResponse:
    """Store or clear one card prompt override.

    An empty or whitespace-only text removes the override, so the prompt is
    built from the template again. A non-empty text is stored in
    ``look_overrides.yaml`` and wins over the template.

    Args:
        data_dir: Root of the runtime data directory, usually ``app_data``.
        profile_id: Profile identifier such as ``dating/profile_2``.
        look_id: Appearance card id from ``looks.yaml``.
        text: Override text or a blank value to clear it.

    Returns:
        The card id, the rebuilt prompt and whether an override is stored.

    Raises:
        ProfileError: The profile or the card is missing or malformed.
        ProfileValidationError: The text is too long.
        ProfileStorageError: The override file cannot be written.
    """
    profile = _load_profile(data_dir, profile_id)
    _find_look(profile, look_id)
    overrides = read_look_overrides(profile.dir)
    if text.strip():
        validate_prompt_text(text)
        overrides[look_id] = text
    else:
        overrides.pop(look_id, None)
    write_look_overrides(profile.dir, overrides)
    updated = _load_profile(data_dir, profile_id)
    updated_look = _find_look(updated, look_id)
    return PromptCardResponse(
        id=updated_look.id,
        prompt=_card_prompt(updated, updated_look),
        overridden=updated_look.prompt is not None,
    )


def write_text_prompt(data_dir: Path, profile_id: str, text: str) -> PromptFile:
    """Replace ``text_prompt.txt`` of a profile with a photo layer.

    Args:
        data_dir: Root of the runtime data directory, usually ``app_data``.
        profile_id: Profile identifier such as ``dating/profile_2``.
        text: New text prompt.

    Returns:
        The written file name and text.

    Raises:
        ProfileError: The profile is missing or malformed, or its recipe has
            no photo layer.
        ProfileValidationError: The text is blank or too long.
        ProfileStorageError: The file cannot be written.
    """
    profile = _load_profile(data_dir, profile_id)
    if not _has_photo_layer(profile.production):
        raise ProfileError(f"у профиля {profile_id} нет фото-слоя")
    validate_prompt_text(text)
    path = profile.dir / TEXT_PROMPT_FILENAME
    write_text_file(path, text)
    return PromptFile(filename=path.name, text=text)


def _load_profile(data_dir: Path, profile_id: str) -> _PromptProfile:
    """Load a validated profile through the generator door."""
    return load_profile(profile_id, data_dir / PROFILES_DIR)


def _card_prompt(profile: _PromptProfile, look: _PromptLook) -> str:
    """Build one card prompt with a fixed seed for a stable API view."""
    _, template = read_template_file(profile.dir)
    return card_prompt(template, look.values, look.prompt, random.Random(0))


def _find_look(profile: _PromptProfile, look_id: str) -> _PromptLook:
    """Return one card of a loaded profile."""
    for look in profile.looks:
        if look.id == look_id:
            return look
    raise ProfileError(f"у профиля {profile.name} нет карточки {look_id!r}")


def _has_photo_layer(layers: Sequence[Layer]) -> bool:
    """Return whether the recipe has a step that produces a photo."""
    return any(layer.output is ArtifactKind.PHOTO for layer in layers)


__all__ = [
    "read_profile_prompts",
    "write_card_prompt",
    "write_prompt_template",
    "write_text_prompt",
]
