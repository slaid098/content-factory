"""Human-readable title and summary of one generator profile."""

from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

from content_factory.config.settings import GeneratorSettings
from content_factory.exceptions.errors import ProfileDescriptionError
from content_factory.exceptions.generator import ProfileError, TextError
from content_factory.schemas.generator.text import TextWriter
from content_factory.schemas.profiles.description import ProfileDescription
from content_factory.schemas.profiles.registry import ProfileInfo
from content_factory.services.generator import TextClient
from content_factory.services.profiles.registry import get_profile
from content_factory.utils.profiles import read_profile_summary, read_profile_title

_PROFILE_FILENAME = "profile.yaml"
_SCENE_PROMPTS = ("prompt_template.txt", "video_prompt.txt")
_TEXT_PROMPT = "text_prompt.txt"
_MAX_FRAGMENT = 20_000
_ATTEMPTS = 2
_FENCE_RE = re.compile(r"^\s*```[^\n]*\n?(.*?)\n?```\s*$", re.DOTALL)


def describe_profile(
    data_dir: Path,
    profile_id: str,
    settings: GeneratorSettings,
    *,
    writer: TextWriter | None = None,
) -> ProfileDescription:
    """Describe one profile with the text model and save both fields.

    A profile with a non-empty ``title`` and ``summary`` is returned as is and
    the model is not called. The prompt is built from ``profile.yaml``, the
    scene prompt (``prompt_template.txt`` or ``video_prompt.txt``) and
    ``text_prompt.txt``; every fragment is capped at 20 000 characters. The
    answer must be a JSON object with both fields, otherwise one retry is
    made. The file is replaced atomically and keeps its permissions, so a
    failed model call or an invalid answer leaves it untouched.

    Args:
        data_dir: Root of the runtime data directory, usually ``app_data``.
        profile_id: Profile identifier such as ``dating/profile_2``.
        settings: Generator settings; the model comes from ``GENERATOR_MODEL``.
        writer: Text model port; defaults to :class:`TextClient`.

    Returns:
        The stored or generated title and summary.

    Raises:
        ProfileDescriptionError: The profile is missing or malformed, the
            model failed twice, or the answer cannot be saved.
    """
    try:
        profile = get_profile(data_dir, profile_id)
    except ProfileError as ex:
        raise ProfileDescriptionError(f"профиль недоступен: {profile_id}") from ex
    if profile.title and profile.summary:
        return ProfileDescription(profile.title, profile.summary)
    data = _read_yaml(profile)
    prompt = _build_prompt(profile, data)
    description = _describe_with_retry(writer or TextClient(settings), prompt, profile.id)
    _write_profile(profile, data, description)
    return description


def _describe_with_retry(
    writer: TextWriter,
    prompt: str,
    profile_id: str,
) -> ProfileDescription:
    """Ask the model and parse the answer, retrying one bad attempt."""
    last = "нет ответа"
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            return _parse(writer.write(prompt), profile_id)
        except (TextError, ProfileDescriptionError) as ex:
            last = str(ex)
            logger.bind(
                profile_id=profile_id,
                attempt=attempt,
                error_type=type(ex).__name__,
            ).warning("Профиль не описан — повтор: {}", last)
    raise ProfileDescriptionError(f"профиль {profile_id} не описан: {last}")


def _parse(raw: str, profile_id: str) -> ProfileDescription:
    """Parse the JSON answer, accepting a Markdown fence around it."""
    try:
        payload: Any = json.loads(_strip_fence(raw))
    except ValueError as ex:
        raise ProfileDescriptionError(f"ответ модели для {profile_id} не JSON") from ex
    if not isinstance(payload, Mapping):
        raise ProfileDescriptionError(f"ответ модели для {profile_id} не объект")
    try:
        title = read_profile_title(payload, profile_id)
        summary = read_profile_summary(payload, profile_id)
    except ProfileError as ex:
        raise ProfileDescriptionError(str(ex)) from ex
    if title is None or summary is None:
        raise ProfileDescriptionError(f"ответ модели для {profile_id} не содержит оба поля")
    return ProfileDescription(title=title, summary=summary)


def _strip_fence(raw: str) -> str:
    """Remove a Markdown code fence from the model answer."""
    match = _FENCE_RE.match(raw)
    return (match.group(1) if match is not None else raw).strip()


def _build_prompt(profile: ProfileInfo, data: Mapping[str, Any]) -> str:
    """Build the model prompt from the profile fields and its prompt files."""
    parts = [
        "Ты описываешь профиль генератора контента Content Factory для оператора.",
        "Верни строго один JSON-объект без markdown, без ``` и без пояснений:",
        '{"title": "...", "summary": "..."}',
        "Требования:",
        "- title: 3..80 символов, одна строка, короткое человекочитаемое название;",
        "- summary: 20..300 символов, одна строка, короткое пояснение о профиле;",
        f"- язык ответа: {profile.language}.",
        "",
        f"language (язык): {profile.language}",
        f"category (категория): {profile.category}",
        f"name (имя): {profile.name}",
        f"niche (ниша): {profile.niche}",
        "",
        "Профиль (profile.yaml):",
        _truncate(yaml.safe_dump(dict(data), allow_unicode=True, sort_keys=False)),
    ]
    scene = _read_fragment(profile, _SCENE_PROMPTS)
    if scene is not None:
        parts.extend(("", "Сцена (prompt_template):", scene))
    text = _read_fragment(profile, (_TEXT_PROMPT,))
    if text is not None:
        parts.extend(("", "Голос и стиль (text_prompt):", text))
    return "\n".join(parts)


def _read_fragment(profile: ProfileInfo, names: tuple[str, ...]) -> str | None:
    """Return the first non-empty prompt file, capped at the fragment limit."""
    for name in names:
        path = profile.dir / name
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as ex:
            raise ProfileDescriptionError(
                f"не удалось прочитать {name} профиля {profile.id}"
            ) from ex
        if text:
            return _truncate(text)
    return None


def _truncate(text: str) -> str:
    """Cut a prompt fragment to the model limit."""
    return text[:_MAX_FRAGMENT]


def _read_yaml(profile: ProfileInfo) -> Mapping[str, Any]:
    """Read the raw ``profile.yaml`` before writing it back."""
    path = profile.dir / _PROFILE_FILENAME
    try:
        payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as ex:
        raise ProfileDescriptionError(
            f"не удалось прочитать profile.yaml профиля {profile.id}"
        ) from ex
    if not isinstance(payload, Mapping):
        raise ProfileDescriptionError(f"profile.yaml профиля {profile.id} должен быть словарём")
    return payload


def _write_profile(
    profile: ProfileInfo,
    data: Mapping[str, Any],
    description: ProfileDescription,
) -> None:
    """Replace ``profile.yaml`` atomically, keeping the other keys and mode."""
    path = profile.dir / _PROFILE_FILENAME
    updated = dict(data)
    updated["title"] = description.title
    updated["summary"] = description.summary
    temporary: Path | None = None
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        descriptor, name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            yaml.safe_dump(updated, handle, allow_unicode=True, sort_keys=False)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        temporary.replace(path)
        temporary = None
    except OSError as ex:
        raise ProfileDescriptionError(
            f"не удалось записать profile.yaml профиля {profile.id}"
        ) from ex
    finally:
        if temporary is not None:
            with suppress(OSError):
                temporary.unlink()


__all__ = ["describe_profile"]
