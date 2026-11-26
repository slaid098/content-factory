"""Loading and validation of a dating generator profile.

A profile is a directory ``<profiles>/<category>/<name>`` that holds the
generation and overlay parameters, appearance cards, prompts, music and,
optionally, its own ComfyUI graph. Shared assets (the LTX graph and fonts)
live in ``<profiles>/_shared``.
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from content_factory.exceptions.generator import GeneratorError, ProfileError
from content_factory.schemas.generator.pipeline import Layer, RecipeProfile
from content_factory.services import production
from content_factory.utils.profiles import (
    read_profile_language,
    read_profile_niche,
    read_profile_positive_int,
)
from content_factory.utils.prompts import (
    card_prompt,
    read_look_overrides,
    read_template_file,
    read_text_prompt_file,
)
from content_factory.utils.run_id import profile_slug

_SHARED_DIR = "_shared"
_FONTS_DIR = "fonts"
_WORKFLOWS_DIR = "workflows"
_DEFAULT_WORKFLOW = "video_gen_workflow.json"
_SUPPORTED_RENDERERS = ("default",)
_AUDIO_SUFFIXES = (".mp3", ".wav")
_DEFAULT_MAX_TEXT_WIDTH = 0.86

_REQUIRED_KEYS = ("count", "width", "height", "frames", "fps", "image", "gpu", "max_cost")
_MAX_IMAGE_SIDE = 8192
_MAX_IMAGE_PIXELS = 16_777_216


@dataclass(frozen=True, slots=True)
class _Look:
    """One appearance card of ``looks.yaml``.

    ``prompt`` is an optional override from ``look_overrides.yaml``; when it
    is set, :func:`build_prompt` returns it verbatim.
    """

    id: str
    values: Mapping[str, object]
    prompt: str | None = None


@dataclass(frozen=True, slots=True)
class _RentOptions:
    """Vast.ai offer filters of one profile."""

    disk_space: int = 80
    gpu_ram: int = 30
    inet_down: int = 500
    inet_up_cost: float = 0.004
    inet_down_cost: float = 0.004


@dataclass(frozen=True, slots=True)
class _OverlayStyle:
    """Text card geometry and colours of one profile."""

    font_size: float = 0.06
    block1_y: float = 0.62
    block2_y: float | None = None
    gap: float = 0.05
    hug_text: bool = False
    max_text_width: float = _DEFAULT_MAX_TEXT_WIDTH
    card_width: float | None = None
    text_pad_x: float = 0.04
    cta_scale: float = 1.0
    cta_divider: bool = False
    merge_blocks: bool = False
    text_color: str = "#000000"
    text_bg_color: str = "#FFFFFF"
    text_bg_alpha: int = 255
    block2_bg: str | None = None
    block2_text: str | None = None
    block2_alpha: int | None = None


@dataclass(frozen=True, slots=True)
class _Profile:
    """Validated generator profile, ready to plan and render a batch."""

    name: str
    language: str
    niche: str
    dir: Path
    count: int
    width: int
    height: int
    frames: int
    frames_jitter: int
    fps: int
    image: str
    gpu: str
    max_cost: float
    font: Path
    renderer: str
    looks: tuple[_Look, ...]
    audio_tracks: tuple[Path, ...]
    workflow: Mapping[str, Any]
    production: tuple[Layer, ...] = production.DEFAULT_RECIPE
    negative_prompt: str | None = None
    cfg: float | None = None
    rent: _RentOptions = _RentOptions()
    style: _OverlayStyle = _OverlayStyle()


def profile_dir(name: str, root: Path) -> Path:
    """Resolve a profile name to its directory.

    Supports both ``dating/profile_2`` and the short ``profile_2`` form. The
    short form is rejected when several categories contain that name.

    Args:
        name: _Profile name from the flow parameter.
        root: Directory of all profiles, usually ``app_data/profiles``.

    Returns:
        Existing profile directory.

    Raises:
        ProfileError: The name is empty, missing or ambiguous.
    """
    clean = name.strip().strip("/")
    if not clean:
        raise ProfileError("пустое имя профиля")
    if "/" in clean:
        path = root / clean
        if not path.is_dir():
            raise ProfileError(f"профиль не найден: {path}")
        return path
    candidates = [path for path in (root / clean, *root.glob(f"*/{clean}")) if path.is_dir()]
    if not candidates:
        raise ProfileError(f"профиль не найден: {clean} (корень {root})")
    if len(candidates) > 1:
        names = ", ".join(path.relative_to(root).as_posix() for path in candidates)
        raise ProfileError(f"имя профиля неоднозначно, укажите категорию: {names}")
    return candidates[0]


def load_profile(name: str, root: Path) -> _Profile:
    """Read and validate a profile before any server is rented.

    Args:
        name: _Profile name, for example ``dating/profile_2``.
        root: Directory of all profiles.

    Returns:
        Frozen profile with resolved fonts, music and the ComfyUI graph.

    Raises:
        ProfileError: A parameter, prompt, card set, font, music file or the
            ComfyUI graph is missing or malformed. All these checks happen
            here, so no paid server is rented for a broken profile.
    """
    directory = profile_dir(name, root)
    data = _read_yaml(directory / "profile.yaml")
    missing = [key for key in _REQUIRED_KEYS if key not in data]
    if missing:
        raise ProfileError(f"в профиле {name} нет обязательных ключей: {missing}")
    renderer = str(data.get("renderer") or "default").strip().lower()
    if renderer not in _SUPPORTED_RENDERERS:
        raise ProfileError(f"renderer={renderer!r} не поддержан в v0: допустим только default")
    profile_name = _profile_name(directory, root)
    width = read_profile_positive_int(data, "width", name)
    height = read_profile_positive_int(data, "height", name)
    _validate_image_size(name, width, height)
    profile = _Profile(
        name=profile_name,
        language=read_profile_language(data, name),
        niche=read_profile_niche(data, name, profile_name.split("/", 1)[0]),
        dir=directory,
        count=read_profile_positive_int(data, "count", name),
        width=width,
        height=height,
        frames=read_profile_positive_int(data, "frames", name),
        frames_jitter=_non_negative_int(data, "frames_jitter", name),
        fps=read_profile_positive_int(data, "fps", name),
        image=_string(data, "image", name),
        gpu=_string(data, "gpu", name),
        max_cost=_positive_float(data, "max_cost", name),
        font=_resolve_font(directory, _string(data, "font", name), root),
        renderer=renderer,
        looks=_read_looks(directory),
        audio_tracks=_audio_tracks(directory, data),
        workflow=_read_workflow(directory, root),
        production=production.read_recipe(data, name),
        negative_prompt=_optional_text(directory / "negative_prompt.txt"),
        cfg=_optional_float(data, "cfg"),
        rent=_RentOptions(
            disk_space=_non_negative_int(data, "disk_space", name, default=80),
            gpu_ram=_non_negative_int(data, "gpu_ram", name, default=30),
            inet_down=_non_negative_int(data, "inet_down", name, default=500),
            inet_up_cost=_non_negative_float(data, "inet_up_cost", default=0.004),
            inet_down_cost=_non_negative_float(data, "inet_down_cost", default=0.004),
        ),
        style=_overlay_style(data),
    )
    prompt_template(profile)
    text_prompt(profile)
    return profile


def _validate_image_size(name: str, width: int, height: int) -> None:
    """Reject photo sizes that cannot be rendered safely on this host.

    The check runs while the profile is loaded, before any renderer or
    ``Image.new`` touches the size, so an oversized profile fails in dry-run
    and normal mode alike instead of exhausting memory.

    Raises:
        ProfileError: A side exceeds :data:`_MAX_IMAGE_SIDE` pixels or the
            photo area exceeds :data:`_MAX_IMAGE_PIXELS` pixels.
    """
    if width > _MAX_IMAGE_SIDE or height > _MAX_IMAGE_SIDE or width * height > _MAX_IMAGE_PIXELS:
        raise ProfileError(
            f"в профиле {name} размер {width}x{height} больше допустимого: "
            f"сторона не больше {_MAX_IMAGE_SIDE}, пикселей не больше {_MAX_IMAGE_PIXELS}"
        )


def profile_niche(profile: _Profile) -> str:
    """Return the stock niche of a loaded profile.

    Args:
        profile: Loaded profile.

    Returns:
        Its niche or, for a profile without one, the profile category.
    """
    return profile.niche or profile.name.split("/", 1)[0]


def parse_names(value: str) -> list[str]:
    """Split the flow parameter into unique profile names.

    Args:
        value: Comma-separated profile names.

    Returns:
        Names in their original order, without blanks and repeats.

    Raises:
        ProfileError: No name is left after trimming.
    """
    names: list[str] = []
    for part in str(value or "").split(","):
        name = part.strip()
        if name and name not in names:
            names.append(name)
    if not names:
        raise ProfileError("не указан ни один профиль")
    return names


def choose_run_id(
    output_dir: Path,
    loaded: Sequence[RecipeProfile],
    moment: datetime,
    *,
    taken: Collection[str] = (),
) -> str:
    """Return a UTC run id whose directories do not exist yet.

    Args:
        output_dir: Root of the run directories.
        loaded: Profiles of the batch, each with its language and name.
        moment: Run start moment.
        taken: Run ids already used by other groups of the same flow run.

    Returns:
        A timestamp id, with a numeric suffix when the directory is taken.

    Raises:
        GeneratorError: A hundred candidates are all taken.
    """
    stamp = moment.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    for suffix in range(1, 100):
        candidate = stamp if suffix == 1 else f"{stamp}-{suffix}"
        if candidate in taken:
            continue
        if not any(
            (output_dir / profile.language / profile_slug(profile) / candidate).exists()
            for profile in loaded
        ):
            return candidate
    raise GeneratorError(f"не удалось подобрать свободный run_id для {stamp}")


def vast_layer(profile: _Profile) -> Layer:
    """Return the Vast video layer the generator executes.

    Raises:
        ProfileError: The recipe has no Vast layer.
    """
    for layer in profile.production:
        if layer.producer == production.VAST_PRODUCER:
            return layer
    raise ProfileError(f"у профиля {profile.name} нет слоя {production.VAST_PRODUCER!r}")


def prompt_template(profile: _Profile) -> str:
    """Return the scene prompt template of the profile.

    Args:
        profile: Loaded profile.

    Returns:
        Text of ``prompt_template.txt`` or, for older profiles, ``video_prompt.txt``.

    Raises:
        ProfileError: Neither prompt file exists or it is empty.
    """
    _, text = read_template_file(profile.dir)
    return text


def build_prompt(profile: _Profile, look: _Look, rng: random.Random) -> str:
    """Fill the scene template with one appearance card.

    A card override is returned verbatim: placeholders and spin options are
    not expanded. Otherwise card fields replace their ``{placeholders}``
    before the ``{a|b}`` spin options are expanded.

    Args:
        profile: Loaded profile.
        look: Appearance card of the planned video.
        rng: Random source for spin options.

    Returns:
        Final scene prompt for the ComfyUI text node.
    """
    _, template = read_template_file(profile.dir)
    return card_prompt(template, look.values, look.prompt, rng)


def text_prompt(profile: _Profile) -> str:
    """Return the system prompt of the text model.

    Args:
        profile: Loaded profile.

    Returns:
        Stripped text of ``text_prompt.txt``.

    Raises:
        ProfileError: ``text_prompt.txt`` is missing or empty.
    """
    _, text = read_text_prompt_file(profile.dir)
    return text.strip()


def _profile_name(directory: Path, root: Path) -> str:
    """Return the POSIX name of the profile relative to the profiles root."""
    try:
        return directory.relative_to(root).as_posix()
    except ValueError:
        return directory.name


def _read_yaml(path: Path) -> dict[str, Any]:
    """Read ``profile.yaml`` as a mapping."""
    try:
        payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as ex:
        raise ProfileError(f"нет profile.yaml: {path}") from ex
    except (OSError, yaml.YAMLError) as ex:
        raise ProfileError(f"не удалось прочитать {path}: {ex}") from ex
    if not isinstance(payload, dict):
        raise ProfileError(f"profile.yaml должен быть словарём: {path}")
    return payload


def _read_workflow(directory: Path, root: Path) -> Mapping[str, Any]:
    """Read the profile graph or fall back to the shared LTX graph."""
    path = directory / "workflow.json"
    if not path.is_file():
        path = root / _SHARED_DIR / _WORKFLOWS_DIR / _DEFAULT_WORKFLOW
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as ex:
        raise ProfileError(f"нет workflow.json ни в профиле, ни в {path}") from ex
    except (OSError, json.JSONDecodeError) as ex:
        raise ProfileError(f"не удалось прочитать workflow {path}: {ex}") from ex
    if not isinstance(payload, dict):
        raise ProfileError(f"workflow должен быть JSON-объектом: {path}")
    return payload


def _read_looks(directory: Path) -> tuple[_Look, ...]:
    """Read appearance cards from ``looks.yaml`` and their prompt overrides."""
    path = directory / "looks.yaml"
    try:
        payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as ex:
        raise ProfileError(f"нет looks.yaml: {path}") from ex
    except (OSError, yaml.YAMLError) as ex:
        raise ProfileError(f"не удалось прочитать {path}: {ex}") from ex
    items = payload.get("looks") if isinstance(payload, dict) else payload
    if not isinstance(items, list) or not items:
        raise ProfileError(f"looks.yaml не содержит карточек: {path}")
    overrides = read_look_overrides(directory)
    looks: list[_Look] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise ProfileError(f"карточка в {path} должна быть словарём")
        look_id = str(item.get("id") or "").strip()
        if not look_id:
            raise ProfileError(f"у карточки в {path} нет id")
        if look_id in seen:
            raise ProfileError(f"повтор id карточки {look_id} в {path}")
        seen.add(look_id)
        looks.append(
            _Look(
                id=look_id,
                values=dict(item),
                prompt=overrides.pop(look_id, None),
            )
        )
    if overrides:
        unknown = ", ".join(sorted(overrides))
        raise ProfileError(f"override неизвестной карточки в {path}: {unknown}")
    return tuple(looks)


def _audio_tracks(directory: Path, data: Mapping[str, Any]) -> tuple[Path, ...]:
    """Collect profile music; ``audio: false`` means a silent final."""
    if not bool(data.get("audio", True)):
        return ()
    folder = directory / str(data.get("audio_dir") or "audio")
    tracks = sorted(
        path for suffix in _AUDIO_SUFFIXES for path in folder.glob(f"*{suffix}") if path.is_file()
    )
    if not tracks:
        raise ProfileError(f"audio включён, но в {folder} нет mp3/wav")
    return tuple(tracks)


def _resolve_font(directory: Path, name: str, root: Path) -> Path:
    """Find a font in the profile folder or in shared fonts."""
    for candidate in (
        directory / _FONTS_DIR / name,
        root / _SHARED_DIR / _FONTS_DIR / name,
    ):
        if candidate.is_file():
            return candidate.resolve()
    raise ProfileError(f"шрифт {name!r} не найден в {directory / _FONTS_DIR} и shared fonts")


def _overlay_style(data: Mapping[str, Any]) -> _OverlayStyle:
    """Build overlay geometry and colours from the profile values."""
    hug_text = _bool_or(data.get("hug_text"), False, "hug_text")
    card_width = _optional_ratio(data, "card_width", 0.2, 0.9)
    if hug_text and card_width is not None:
        raise ProfileError("hug_text и card_width взаимоисключающи: выберите один режим карточки")
    return _OverlayStyle(
        font_size=_float_or(data.get("size"), 0.06, "size"),
        block1_y=_float_or(data.get("block1_y"), 0.62, "block1_y"),
        block2_y=_optional_number(data.get("block2_y")),
        gap=_ratio(data, "gap", 0.05, 0, 1),
        hug_text=hug_text,
        max_text_width=_ratio(data, "max_text_width", _DEFAULT_MAX_TEXT_WIDTH, 0.2, 0.9),
        card_width=card_width,
        text_pad_x=_ratio(data, "text_pad_x", 0.04, 0.01, 0.10),
        cta_scale=_ratio(data, "cta_scale", 1.0, 0.8, 2.0),
        cta_divider=_bool_or(data.get("cta_divider"), False, "cta_divider"),
        merge_blocks=_bool_or(data.get("merge_blocks"), False, "merge_blocks"),
        text_color=str(data.get("text_color") or "#000000"),
        text_bg_color=str(data.get("text_bg_color") or "#FFFFFF"),
        text_bg_alpha=_alpha(data.get("text_bg_alpha")),
        block2_bg=_optional_str(data.get("block2_bg")),
        block2_text=_optional_str(data.get("block2_text")),
        block2_alpha=_optional_int(data.get("block2_alpha")),
    )


def _string(data: Mapping[str, Any], key: str, name: str) -> str:
    """Required meaningful string value."""
    value = str(data.get(key) or "").strip()
    if not value:
        raise ProfileError(f"в профиле {name} пустой ключ {key}")
    return value


def _non_negative_int(
    data: Mapping[str, Any], key: str, name: str, *, default: int | None = None
) -> int:
    """Optional non-negative integer, falling back to *default*."""
    raw = data.get(key, default)
    if raw is None:
        raise ProfileError(f"в профиле {name} нет ключа {key}")
    try:
        value = int(raw)
    except (TypeError, ValueError) as ex:
        raise ProfileError(f"в профиле {name} {key} должен быть числом") from ex
    if value < 0:
        raise ProfileError(f"в профиле {name} {key} не может быть отрицательным")
    return value


def _positive_float(data: Mapping[str, Any], key: str, name: str) -> float:
    """Required positive float value."""
    try:
        value = float(data.get(key, 0))
    except (TypeError, ValueError) as ex:
        raise ProfileError(f"в профиле {name} {key} должен быть числом") from ex
    if value <= 0:
        raise ProfileError(f"в профиле {name} {key} должен быть больше нуля")
    return value


def _non_negative_float(data: Mapping[str, Any], key: str, *, default: float) -> float:
    """Optional non-negative float."""
    try:
        value = float(data.get(key, default))
    except (TypeError, ValueError) as ex:
        raise ProfileError(f"{key} должен быть числом") from ex
    if value < 0:
        raise ProfileError(f"{key} не может быть отрицательным")
    return value


def _optional_float(data: Mapping[str, Any], key: str) -> float | None:
    """Optional float value, normalized to ``None`` when absent."""
    raw = data.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError) as ex:
        raise ProfileError(f"{key} должен быть числом") from ex


def _optional_text(path: Path) -> str | None:
    """Read an optional prompt file, skipping an empty one."""
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def _optional_str(value: object) -> str | None:
    """Optional stripped string."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    """Optional integer."""
    if value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError) as ex:
        raise ProfileError(f"ожидалось целое число, получено {value!r}") from ex


def _optional_number(value: object) -> float | None:
    """Optional float read from a profile value."""
    if value is None:
        return None
    try:
        number = float(str(value))
    except (TypeError, ValueError) as ex:
        raise ProfileError(f"ожидалось число, получено {value!r}") from ex
    if not math.isfinite(number):
        raise ProfileError(f"ожидалось конечное число, получено {value!r}")
    return number


def _alpha(value: object) -> int:
    """Opacity of the text background, defaulting to fully opaque."""
    if value is None:
        return 255
    try:
        return max(0, min(255, int(str(value))))
    except (TypeError, ValueError) as ex:
        raise ProfileError(f"ожидалась непрозрачность 0..255, получено {value!r}") from ex


def _float_or(value: object, default: float, key: str) -> float:
    """Float value or *default* when absent; a bad value names its key."""
    if value is None:
        return default
    try:
        number = float(str(value))
    except (TypeError, ValueError) as ex:
        raise ProfileError(f"{key} должен быть числом: {value!r}") from ex
    if not math.isfinite(number):
        raise ProfileError(f"{key} должен быть конечным числом: {value!r}")
    return number


def _ratio(data: Mapping[str, Any], key: str, default: float, low: float, high: float) -> float:
    """Float value in ``low..high``; a bad value names its key and range."""
    value = _float_or(data.get(key), default, key)
    if not low <= value <= high:
        raise ProfileError(f"{key} должен быть в диапазоне {low}..{high}: {value}")
    return value


def _optional_ratio(data: Mapping[str, Any], key: str, low: float, high: float) -> float | None:
    """Optional float value in ``low..high``; absent stays ``None``."""
    if data.get(key) is None:
        return None
    return _ratio(data, key, low, low, high)


def _bool_or(value: object, default: bool, key: str) -> bool:
    """Strict boolean value or *default* when absent."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise ProfileError(f"{key} должен быть true или false: {value!r}")


__all__ = [
    "build_prompt",
    "choose_run_id",
    "load_profile",
    "parse_names",
    "profile_dir",
    "profile_niche",
    "profile_slug",
    "prompt_template",
    "text_prompt",
    "vast_layer",
]
