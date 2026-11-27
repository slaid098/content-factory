"""Loading and creation of overlay profiles.

An overlay profile is a directory ``<profiles>/overlay/profile_N`` with a
``profile.yaml`` and ten prompts in ``backgrounds.txt``. It has no
``looks.yaml``, text card, music or dating language: one prompt is one silent
clip of particles on a pure black background.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from content_factory.exceptions.generator import ProfileError
from content_factory.services.generator.overlay_prompts import read_prompts, validate_prompts
from content_factory.services.generator.profiles import (
    _SHARED_DIR,
    _WORKFLOWS_DIR,
    _RentOptions,
    profile_dir,
)

OVERLAY_CATEGORY = "overlay"
OVERLAY_WORKFLOW = "workflow-overlay.json"
DEFAULT_MIN_DENSITY = 3.0
DEFAULT_WIDTH = 384
DEFAULT_HEIGHT = 672
DEFAULT_FRAMES = 89
DEFAULT_FPS = 18
DEFAULT_GPU = "L40S"
DEFAULT_IMAGE = "ghcr.io/slaid099/video-gen-2:ltx-2.5"
DEFAULT_MAX_COST = 1.0

_MAX_SIDE = 8192
_MAX_PIXELS = 16_777_216
_NAME_RE = re.compile(r"^(?P<category>[a-z0-9_]+)/profile_(?P<number>\d+)$")
_REQUIRED = ("count", "width", "height", "frames", "fps", "image", "gpu", "max_cost")
_WORKFLOW_NODES = ("5", "8", "9", "10", "11", "15")


@dataclass(frozen=True, slots=True)
class OverlayProfile:
    """Validated overlay profile, ready to plan and generate clips.

    The fields ``name``, ``gpu``, ``image``, ``max_cost`` and ``rent`` are
    exactly what :class:`~content_factory.services.generator.vast.VastSession`
    reads, so the same rental, journal and teardown code serves overlays.
    """

    name: str
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
    rent: _RentOptions
    workflow: Mapping[str, Any]
    prompts: tuple[str, ...]
    min_density: float = DEFAULT_MIN_DENSITY
    negative_prompt: str | None = None
    cfg: float | None = None

    @property
    def seconds(self) -> float:
        """Return the nominal clip length in seconds."""
        return self.frames / self.fps

    @property
    def slug(self) -> str:
        """Return the name safe for a file name, such as ``overlay_profile_17``."""
        return self.name.replace("/", "_")


def load_overlay_profile(name: str, root: Path) -> OverlayProfile:
    """Read and validate an overlay profile before any server is rented.

    Args:
        name: Profile name, for example ``overlay/profile_17``.
        root: Directory of all profiles, usually ``app_data/profiles``.

    Returns:
        Frozen profile with prompts and the shared overlay ComfyUI graph.

    Raises:
        ProfileError: A parameter is missing or invalid, the prompt count
            differs from ``count``, or the shared graph has no required node.
    """
    directory = profile_dir(name, root)
    data = _read_yaml(directory / "profile.yaml")
    missing = [key for key in _REQUIRED if key not in data]
    if missing:
        raise ProfileError(f"в overlay-профиле {name} нет обязательных ключей: {missing}")
    profile_name = f"{directory.parent.name}/{directory.name}"
    width = _positive_int(data, "width", profile_name)
    height = _positive_int(data, "height", profile_name)
    _validate_size(profile_name, width, height)
    count = _positive_int(data, "count", profile_name)
    prompts = read_prompts(directory / "backgrounds.txt")
    if len(prompts) != count:
        raise ProfileError(
            f"в overlay-профиле {profile_name} промптов {len(prompts)}, а count={count}"
        )
    workflow = _read_workflow(root)
    return OverlayProfile(
        name=profile_name,
        dir=directory,
        count=count,
        width=width,
        height=height,
        frames=_positive_int(data, "frames", profile_name),
        frames_jitter=_non_negative_int(data, "frames_jitter", profile_name),
        fps=_positive_int(data, "fps", profile_name),
        image=_text(data, "image", profile_name),
        gpu=_text(data, "gpu", profile_name),
        max_cost=_positive_float(data, "max_cost", profile_name),
        rent=_rent(data, profile_name),
        workflow=workflow,
        prompts=prompts,
        min_density=_positive_float(data, "min_density", profile_name, DEFAULT_MIN_DENSITY),
        negative_prompt=_optional_text(directory / "negative_prompt.txt"),
        cfg=_optional_float(data, "cfg"),
    )


def create_overlay_profile(
    root: Path,
    name: str,
    prompts: Sequence[str],
) -> Path:
    """Write a new overlay profile and return its directory.

    The profile is created under ``<root>/overlay`` and inherits the format
    of the first profile: 384x672, 89 frames, 18 fps and the LTX overlay
    image. A profile is disposable: ten prompts, ten clips, one channel
    portion.

    Args:
        root: Directory of all profiles, usually ``app_data/profiles``.
        name: ``overlay/profile_17`` or the short ``profile_17``.
        prompts: Prompts in production order; their number becomes ``count``.

    Returns:
        The new profile directory.

    Raises:
        ProfileError: The name has another category or shape, the directory
            already exists, or the prompts are invalid.
    """
    category, number = _parse_name(name)
    validate_prompts(prompts, len(prompts))
    directory = root / category / f"profile_{number}"
    try:
        directory.mkdir(parents=True, exist_ok=False)
    except FileExistsError as ex:
        raise ProfileError(f"профиль уже существует: {directory}") from ex
    _write_yaml(directory / "profile.yaml", _profile_yaml(len(prompts)))
    _write_prompts(directory / "backgrounds.txt", prompts)
    (directory / "video_prompt.txt").write_text("{background}\n", encoding="utf-8")
    (directory / "phrases.txt").write_text("", encoding="utf-8")
    return directory


def next_profile_name(root: Path) -> str:
    """Return the first free ``overlay/profile_N`` name."""
    directory = root / OVERLAY_CATEGORY
    numbers = [
        int(match.group(1))
        for path in directory.glob("profile_*")
        if path.is_dir() and (match := re.fullmatch(r"profile_(\d+)", path.name))
    ]
    return f"{OVERLAY_CATEGORY}/profile_{max(numbers, default=0) + 1}"


def _read_yaml(path: Path) -> dict[str, Any]:
    """Read a profile YAML or raise a domain error."""
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as ex:
        raise ProfileError(f"не удалось прочитать {path}: {ex}") from ex
    if not isinstance(payload, dict):
        raise ProfileError(f"в {path} ожидался словарь")
    return payload


def _write_yaml(path: Path, payload: Mapping[str, Any]) -> None:
    """Write a profile YAML with stable key order and UTF-8 text."""
    path.write_text(
        yaml.safe_dump(dict(payload), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _profile_yaml(count: int) -> dict[str, Any]:
    """Return the parameters of a new overlay profile."""
    return {
        "count": count,
        "width": DEFAULT_WIDTH,
        "height": DEFAULT_HEIGHT,
        "frames": DEFAULT_FRAMES,
        "frames_jitter": 0,
        "fps": DEFAULT_FPS,
        "gpu": DEFAULT_GPU,
        "image": DEFAULT_IMAGE,
        "max_cost": DEFAULT_MAX_COST,
        "disk_space": 80,
        "gpu_ram": 30,
        "inet_down": 500,
        "inet_up_cost": 0.02,
        "inet_down_cost": 0.02,
        "min_density": DEFAULT_MIN_DENSITY,
        "title": f"Оверлеи: {count} клипов плотных частиц на чёрном фоне",
        "summary": (
            f"{count} беззвучных клипов 384x672 на {DEFAULT_FRAMES} кадров при {DEFAULT_FPS} fps: "
            "частицы заполняют кадр, фон чистый чёрный."
        ),
    }


def _write_prompts(path: Path, prompts: Sequence[str]) -> None:
    """Write prompts with the header that explains the mandatory tail."""
    header = (
        "# Оверлеи канала: плотные частицы на чистом чёрном фоне, один вариант на строку.\n"
        "# Строка целиком уходит в промпт (video_prompt.txt = {background}).\n"
        "# Хвост обязательный: black background без движения камеры, иначе LTX дорисует сцену.\n"
        "# Дедуп против остальных профилей — сервис оверлеев, порог 0.85.\n"
    )
    path.write_text(header + "\n".join(prompts) + "\n", encoding="utf-8")


def _parse_name(name: str) -> tuple[str, int]:
    """Parse a profile name and reject categories other than ``overlay``."""
    clean = name.strip().strip("/")
    if "/" not in clean:
        clean = f"{OVERLAY_CATEGORY}/{clean}"
    match = _NAME_RE.fullmatch(clean)
    if match is None or match.group("category") != OVERLAY_CATEGORY:
        raise ProfileError(f"недопустимое имя overlay-профиля {name!r}: нужно overlay/profile_N")
    return OVERLAY_CATEGORY, int(match.group("number"))


def _read_workflow(root: Path) -> Mapping[str, Any]:
    """Read the shared overlay graph and require every node the builder edits."""
    path = root / _SHARED_DIR / _WORKFLOWS_DIR / OVERLAY_WORKFLOW
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as ex:
        raise ProfileError(f"не удалось прочитать overlay-workflow {path}: {ex}") from ex
    if not isinstance(payload, dict):
        raise ProfileError(f"в {path} ожидался JSON-объект")
    absent = [node for node in _WORKFLOW_NODES if node not in payload]
    if absent:
        raise ProfileError(f"в overlay-workflow нет узлов {absent}")
    return payload


def _rent(data: Mapping[str, Any], name: str) -> _RentOptions:
    """Read the Vast.ai offer filters of an overlay profile."""
    return _RentOptions(
        disk_space=_non_negative_int(data, "disk_space", name, 80),
        gpu_ram=_non_negative_int(data, "gpu_ram", name, 30),
        inet_down=_non_negative_int(data, "inet_down", name, 500),
        inet_up_cost=_non_negative_float(data, "inet_up_cost", 0.02),
        inet_down_cost=_non_negative_float(data, "inet_down_cost", 0.02),
    )


def _text(data: Mapping[str, Any], key: str, name: str) -> str:
    """Return a non-empty string parameter."""
    value = str(data.get(key) or "").strip()
    if not value:
        raise ProfileError(f"в overlay-профиле {name} пустой {key}")
    return value


def _positive_int(data: Mapping[str, Any], key: str, name: str) -> int:
    """Return a positive integer parameter."""
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ProfileError(f"в overlay-профиле {name} {key} должен быть целым больше нуля")
    return value


def _non_negative_int(
    data: Mapping[str, Any],
    key: str,
    name: str,
    default: int = 0,
) -> int:
    """Return a non-negative integer parameter or *default*."""
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ProfileError(f"в overlay-профиле {name} {key} должен быть целым не меньше нуля")
    return value


def _positive_float(
    data: Mapping[str, Any],
    key: str,
    name: str,
    default: float | None = None,
) -> float:
    """Return a positive finite float parameter or *default*."""
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ProfileError(f"в overlay-профиле {name} {key} должен быть числом больше нуля")
    return float(value)


def _non_negative_float(data: Mapping[str, Any], key: str, default: float) -> float:
    """Return a non-negative finite float parameter or *default*."""
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise ProfileError(f"{key} должен быть числом не меньше нуля")
    return float(value)


def _optional_float(data: Mapping[str, Any], key: str) -> float | None:
    """Return an optional positive float parameter."""
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ProfileError(f"{key} должен быть числом больше нуля")
    return float(value)


def _optional_text(path: Path) -> str | None:
    """Return the stripped file text when the file exists and is non-empty."""
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8").strip()
    return text or None


def _validate_size(name: str, width: int, height: int) -> None:
    """Reject an overlay size that could exhaust memory in ffmpeg or ComfyUI."""
    if width > _MAX_SIDE or height > _MAX_SIDE or width * height > _MAX_PIXELS:
        raise ProfileError(f"в overlay-профиле {name} размер {width}x{height} слишком велик")


__all__ = [
    "DEFAULT_MIN_DENSITY",
    "OVERLAY_CATEGORY",
    "OverlayProfile",
    "create_overlay_profile",
    "load_overlay_profile",
    "next_profile_name",
]
