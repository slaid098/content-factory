"""Fixture profiles and fake ports for generator tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from content_factory.schemas.generator.server import ServerConnection, ServerInfo
from content_factory.services.generator.profiles import _Profile, load_profile

SHARED_WORKFLOW: dict[str, Any] = {
    "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "ltx.safetensors"}},
    "5": {
        "class_type": "EmptyLTXVLatentVideo",
        "inputs": {"width": 1, "height": 1, "length": 1, "batch_size": 1},
    },
    "6": {
        "class_type": "LTXVEmptyLatentAudio",
        "inputs": {"frames_number": 1, "frame_rate": 1, "batch_size": 1},
    },
    "8": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
    "9": {"class_type": "CLIPTextEncode", "inputs": {"text": "", "clip": ["2", 0]}},
    "10": {
        "class_type": "LTXVConditioning",
        "inputs": {"frame_rate": 1, "positive": ["8", 0], "negative": ["9", 0]},
    },
    "11": {
        "class_type": "KSampler",
        "inputs": {"seed": 0, "steps": 6, "cfg": 1, "sampler_name": "euler"},
    },
    "15": {
        "class_type": "VHS_VideoCombine",
        "inputs": {"frame_rate": 1, "filename_prefix": "speech", "save_metadata": True},
    },
}


def make_profile(
    root: Path,
    name: str = "dating/profile_2",
    *,
    looks: int = 3,
    renderer: str = "default",
    audio: bool = True,
    workflow: bool = False,
    block2_y: bool = True,
    frames_jitter: int = 11,
    hug_text: bool | str = False,
    max_text_width: float | str | None = None,
) -> Path:
    """Create a small profile under *root* and return its directory."""
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "prompt_template.txt").write_text(
        "A {age}-year-old woman, {age_text}, {hair_color_text}, {pose_text}. "
        "Shot in {day|evening} light.",
        encoding="utf-8",
    )
    (directory / "text_prompt.txt").write_text("Сгенерируй текст карточки.", encoding="utf-8")
    (directory / "negative_prompt.txt").write_text("blurry, low quality", encoding="utf-8")
    (directory / "looks.yaml").write_text(
        yaml.safe_dump(
            {
                "looks": [
                    {
                        "id": f"c{index:02d}",
                        "age": 25 + index,
                        "hair_color_text": "dark brown",
                        "pose_text": "standing still",
                    }
                    for index in range(1, looks + 1)
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    data: dict[str, Any] = {
        "count": 20,
        "width": 512,
        "height": 896,
        "frames": 119,
        "frames_jitter": frames_jitter,
        "fps": 24,
        "gpu": "L40S",
        "image": "ghcr.io/slaid099/video-gen-2:ltx-2.5",
        "max_cost": 1.0,
        "font": "arialbd.ttf",
        "renderer": renderer,
        "audio": audio,
        "niche": "dating",
        "language": "ru",
        "size": 0.06,
        "block1_y": 0.62,
        "gap": 0.05,
        "text_bg_color": "#FFF1DC",
        "text_color": "#3B2A20",
        "text_bg_alpha": 215,
    }
    if block2_y:
        data["block2_y"] = 0.78
        data["block2_bg"] = "#A9552F"
        data["block2_text"] = "#FFF8EF"
        data["block2_alpha"] = 240
    if hug_text:
        data["hug_text"] = hug_text
    if max_text_width is not None:
        data["max_text_width"] = max_text_width
    (directory / "profile.yaml").write_text(
        yaml.safe_dump(data, allow_unicode=True), encoding="utf-8"
    )
    if audio:
        audio_dir = directory / "audio"
        audio_dir.mkdir()
        (audio_dir / "neutral_1.mp3").write_bytes(b"music")
    fonts = root / "_shared" / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    (fonts / "arialbd.ttf").write_bytes(b"font")
    if workflow:
        (directory / "workflow.json").write_text(json.dumps(SHARED_WORKFLOW), encoding="utf-8")
    else:
        shared = root / "_shared" / "workflows"
        shared.mkdir(parents=True, exist_ok=True)
        (shared / "video_gen_workflow.json").write_text(
            json.dumps(SHARED_WORKFLOW), encoding="utf-8"
        )
    return directory


def fixture_profile(root: Path, **kwargs: Any) -> _Profile:
    """Create and load a fixture profile."""
    make_profile(root, **kwargs)
    return load_profile("dating/profile_2", root)


class FakeSession:
    """Server session that records open and destroy calls."""

    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events if events is not None else []
        self.opened = 0
        self.destroyed = 0
        self.connection = ServerConnection(
            contract_id="42",
            ip="127.0.0.1",
            port=8188,
            gpu="L40S",
            image="image",
        )

    def open(self) -> ServerInfo:
        """Record the rent and return a ready server."""
        self.events.append("open")
        self.opened += 1
        return ServerInfo(connection=self.connection, cost_per_hour=0.5)

    def destroy(self) -> None:
        """Record the verified teardown."""
        self.events.append("destroy")
        self.destroyed += 1
