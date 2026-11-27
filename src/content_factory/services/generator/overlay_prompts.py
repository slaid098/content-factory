"""Overlay prompt validation and near-duplicate detection.

Every overlay prompt is an English description of dense particles on a pure
black background. The mandatory tail is shared by every prompt, so it is cut
off before comparison: otherwise even unrelated scenes look similar. Two
prompts are compared by their token sets and the Levenshtein ratio of the
remaining words, which catches reordered words and close synonyms.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

from content_factory.exceptions.generator import ProfileError
from content_factory.schemas.generator.overlay import PromptConflict

BLOCK_RATIO = 0.85
WARN_RATIO = 0.75
TAIL_MARK = "pure black background"

_TAIL_RE = re.compile(r"\s*(?:on|against)?\s*pure black background\b.*$", re.IGNORECASE)
_CYRILLIC_RE = re.compile(r"[а-яёА-ЯЁ]")
_NON_WORDS_RE = re.compile(r"[^a-z0-9 ]")


def read_prompts(path: Path) -> tuple[str, ...]:
    """Read prompts from a text file, one prompt per line.

    Blank lines and lines starting with ``#`` are comments and are skipped.
    A missing file gives an empty tuple, so a caller can create the file
    before the profile exists.
    """
    if not path.is_file():
        return ()
    return tuple(
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    )


def prompt_body(prompt: str) -> str:
    """Return the comparable part of a prompt without the shared tail."""
    stripped = _TAIL_RE.sub("", prompt).lower()
    return " ".join(_NON_WORDS_RE.sub(" ", stripped).split())


def token_set_ratio(left: str, right: str) -> float:
    """Return the similarity of two prompt bodies in ``0..1``.

    Common words form one block; the words unique to each side are compared
    by :class:`difflib.SequenceMatcher`. The result is symmetric and ignores
    word order, unlike a plain character ratio.
    """
    if not left or not right:
        return 0.0
    left_set, right_set = set(left.split()), set(right.split())
    common = " ".join(sorted(left_set & right_set))
    rest_left = " ".join(sorted(left_set - right_set))
    rest_right = " ".join(sorted(right_set - left_set))
    one = f"{common} {rest_left}".strip()
    two = f"{common} {rest_right}".strip()
    if not one or not two:
        return 0.0
    return difflib.SequenceMatcher(None, one, two).ratio()


def load_existing(root: Path, *, exclude: str | None = None) -> dict[str, tuple[str, ...]]:
    """Load prompts of every overlay profile under *root*.

    Args:
        root: Directory with ``profile_*`` folders, usually
            ``app_data/profiles/overlay``.
        exclude: Profile folder name to skip, for example the profile being
            checked against the others.

    Returns:
        Folder name to prompts, in folder order.
    """
    if not root.is_dir():
        return {}
    found: dict[str, tuple[str, ...]] = {}
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        if directory.name == exclude:
            continue
        prompts = read_prompts(directory / "backgrounds.txt")
        if prompts:
            found[directory.name] = prompts
    return found


def check_prompts(
    prompts: Sequence[str],
    existing: Mapping[str, Sequence[str]],
) -> tuple[PromptConflict, ...]:
    """Compare *prompts* with every existing prompt of the catalogue.

    Every pair at or above :data:`WARN_RATIO` is reported, so the caller can
    show warnings and refuse blocking conflicts. The result is ordered from
    the closest pair down.
    """
    conflicts: list[PromptConflict] = []
    for prompt in prompts:
        body = prompt_body(prompt)
        for profile, lines in existing.items():
            for line, other in enumerate(lines, start=1):
                ratio = token_set_ratio(body, prompt_body(other))
                if ratio < WARN_RATIO:
                    continue
                conflicts.append(
                    PromptConflict(
                        prompt=prompt,
                        other_profile=profile,
                        other_line=line,
                        ratio=round(ratio, 3),
                        blocking=ratio >= BLOCK_RATIO,
                    )
                )
    conflicts.extend(_internal_conflicts(prompts))
    conflicts.sort(key=lambda item: item.ratio, reverse=True)
    return tuple(conflicts)


def _internal_conflicts(prompts: Sequence[str]) -> list[PromptConflict]:
    """Report candidate prompts that are too close to another candidate."""
    bodies = [prompt_body(prompt) for prompt in prompts]
    found: list[PromptConflict] = []
    for left in range(len(prompts)):
        for right in range(left + 1, len(prompts)):
            ratio = token_set_ratio(bodies[left], bodies[right])
            if ratio < WARN_RATIO:
                continue
            found.append(
                PromptConflict(
                    prompt=prompts[left],
                    other_profile="<новый>",
                    other_line=right + 1,
                    ratio=round(ratio, 3),
                    blocking=ratio >= BLOCK_RATIO,
                )
            )
    return found


def validate_prompts(prompts: Sequence[str], count: int) -> None:
    """Reject prompts that cannot be generated as an overlay profile.

    Args:
        prompts: Candidate prompts in production order.
        count: Exact number of clips the profile must declare.

    Raises:
        ProfileError: The count differs, a prompt is blank or non-Latin, the
            mandatory tail is missing, or two prompts are the same.
    """
    if count <= 0:
        raise ProfileError(f"count должен быть больше нуля, получено {count}")
    if len(prompts) != count:
        raise ProfileError(f"промптов {len(prompts)}, нужно ровно {count}")
    seen: set[str] = set()
    for number, prompt in enumerate(prompts, start=1):
        text = prompt.strip()
        if not text:
            raise ProfileError(f"промпт {number}: пустая строка")
        if not text.isascii():
            raise ProfileError(f"промпт {number}: только латиница, LTX не понимает другие буквы")
        if _CYRILLIC_RE.search(text):
            raise ProfileError(f"промпт {number}: кириллица в промпте")
        if TAIL_MARK not in text.lower():
            raise ProfileError(f"промпт {number}: нет обязательного хвоста {TAIL_MARK!r}")
        body = prompt_body(text)
        if body in seen:
            raise ProfileError(f"промпт {number}: повтор другого промпта профиля")
        seen.add(body)


def blocking(conflicts: Sequence[PromptConflict]) -> tuple[PromptConflict, ...]:
    """Return only the conflicts that must stop profile creation."""
    return tuple(item for item in conflicts if item.blocking)


__all__ = [
    "BLOCK_RATIO",
    "TAIL_MARK",
    "WARN_RATIO",
    "blocking",
    "check_prompts",
    "load_existing",
    "prompt_body",
    "read_prompts",
    "token_set_ratio",
    "validate_prompts",
]
