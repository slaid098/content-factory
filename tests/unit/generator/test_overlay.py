"""_Overlay layout: one card at block1_y, the second at block2_y or gap."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from PIL import ImageFont
from tests.unit.generator.helpers import fixture_profile

from content_factory.exceptions.generator import OverlayError
from content_factory.services.generator import overlay as overlay_module
from content_factory.services.generator.overlay import block_boxes, render_card
from content_factory.services.generator.texts import _Overlay, _TextBlock


def _patch_font(monkeypatch) -> None:
    monkeypatch.setattr(
        overlay_module,
        "_load_font",
        lambda path, size: ImageFont.load_default(),
    )


def test_two_blocks_use_profile_positions(tmp_path: Path, monkeypatch) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    _patch_font(monkeypatch)
    card = _Overlay(
        blocks=(
            _TextBlock(lines=("Основной текст",)),
            _TextBlock(lines=("напиши мне",)),
        )
    )

    boxes = block_boxes(profile, card)

    assert len(boxes) == 2
    assert abs(boxes[0].top + boxes[0].height / 2 - profile.style.block1_y * profile.height) <= 1
    assert abs(boxes[1].top + boxes[1].height / 2 - 0.78 * profile.height) <= 1
    assert boxes[1].background == (169, 85, 47, 240)


def test_second_block_falls_back_to_gap(tmp_path: Path, monkeypatch) -> None:
    profile = fixture_profile(tmp_path / "profiles", block2_y=False)
    _patch_font(monkeypatch)
    card = _Overlay(blocks=(_TextBlock(lines=("Первый",)), _TextBlock(lines=("Второй",))))

    boxes = block_boxes(profile, card)

    assert profile.style.block2_y is None
    expected = (profile.style.block1_y + profile.style.gap) * profile.height
    assert abs(boxes[1].top + boxes[1].height / 2 - expected) <= 1


def test_second_block_zero_alpha_is_kept(tmp_path: Path, monkeypatch) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    profile = replace(profile, style=replace(profile.style, block2_alpha=0))
    _patch_font(monkeypatch)
    card = _Overlay(blocks=(_TextBlock(lines=("Первый",)), _TextBlock(lines=("Второй",))))

    boxes = block_boxes(profile, card)

    assert boxes[1].background[3] == 0


def test_single_block_is_rendered_as_transparent_card(tmp_path: Path, monkeypatch) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    _patch_font(monkeypatch)
    card = _Overlay(blocks=(_TextBlock(lines=("Одна карточка",)),))

    boxes = block_boxes(profile, card)
    image = render_card(profile, card)

    assert len(boxes) == 1
    assert boxes[0].top > 0
    assert image.size == (profile.width, profile.height)
    assert image.mode == "RGBA"
    assert image.getpixel((0, 0))[3] == 0


def test_missing_font_is_reported(tmp_path: Path, monkeypatch) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    card = _Overlay(blocks=(_TextBlock(lines=("Текст",)),))
    broken = replace(profile, font=tmp_path / "missing.ttf")

    with pytest.raises(OverlayError, match="шрифт"):
        render_card(broken, card)


def test_mix_writes_card_and_calls_ffmpeg_with_music(tmp_path: Path, monkeypatch) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    _patch_font(monkeypatch)
    commands: list[list[str]] = []
    monkeypatch.setattr(overlay_module.media, "run", lambda command: commands.append(command))
    card = _Overlay(blocks=(_TextBlock(lines=("Текст",)),))
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    output = tmp_path / "final" / "video.mp4"
    audio = profile.audio_tracks[0]

    overlay_module.mix(profile, card, source, output, audio)

    assert commands
    assert "-stream_loop" in commands[0]
    assert str(audio) in commands[0]
    assert not (output.parent / "video.card.png").exists()


def test_mix_without_music_keeps_the_video_silent(tmp_path: Path, monkeypatch) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    _patch_font(monkeypatch)
    commands: list[list[str]] = []
    monkeypatch.setattr(overlay_module.media, "run", lambda command: commands.append(command))
    card = _Overlay(blocks=(_TextBlock(lines=("Текст",)),))
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")

    overlay_module.mix(profile, card, source, tmp_path / "out.mp4", None)

    assert "-an" in commands[0]
    assert "-stream_loop" not in commands[0]


def test_mix_reports_a_broken_card(tmp_path: Path, monkeypatch) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    card = _Overlay(blocks=(_TextBlock(lines=("Текст",)),))

    def broken(profile: object, card: object) -> object:
        raise OSError("no space")

    monkeypatch.setattr(overlay_module, "render_card", broken)

    with pytest.raises(OverlayError, match="не удалось подготовить карточку"):
        overlay_module.mix(
            profile,
            card,
            tmp_path / "source.mp4",
            tmp_path / "out.mp4",
            None,
        )


def test_wrap_splits_long_lines_by_width() -> None:
    lines = overlay_module._wrap(("one two three", "single"), ImageFont.load_default(), 1)

    assert lines == ("one", "two", "three", "single")


def test_colours_are_parsed_and_validated() -> None:
    assert overlay_module._rgb("#A9552F") == (169, 85, 47)
    assert overlay_module._rgb("000000") == (0, 0, 0)
    assert overlay_module._rgba("#FFFFFF", 300) == (255, 255, 255, 255)
    assert overlay_module._rgba("#FFFFFF", -1) == (255, 255, 255, 0)

    with pytest.raises(OverlayError, match="неверный цвет"):
        overlay_module._rgb("#GGGGGG")


def test_blocks_without_text_are_skipped(tmp_path: Path, monkeypatch) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    _patch_font(monkeypatch)
    card = _Overlay(blocks=(_TextBlock(lines=("   ",)),))

    assert block_boxes(profile, card) == ()


def test_hug_text_card_is_as_wide_as_the_text(tmp_path: Path, monkeypatch) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    profile = replace(profile, style=replace(profile.style, hug_text=True))
    _patch_font(monkeypatch)
    card = _Overlay(blocks=(_TextBlock(lines=("Коротко",)),))

    boxes = block_boxes(profile, card)

    assert boxes[0].width < profile.width // 2
    assert abs(boxes[0].left - (profile.width - boxes[0].width) / 2) <= 1


def test_hug_text_wraps_at_profile_text_width(tmp_path: Path, monkeypatch) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    profile = replace(profile, style=replace(profile.style, hug_text=True, max_text_width=0.25))
    _patch_font(monkeypatch)
    card = _Overlay(
        blocks=(_TextBlock(lines=("one two three four five six seven eight nine ten",)),)
    )

    boxes = block_boxes(profile, card)

    assert len(boxes[0].lines) > 1


def test_font_below_twenty_pixels_is_kept(tmp_path: Path, monkeypatch) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    profile = replace(profile, style=replace(profile.style, font_size=0.038))
    _patch_font(monkeypatch)
    card = _Overlay(blocks=(_TextBlock(lines=("Текст",)),))

    boxes = block_boxes(profile, card)

    assert boxes[0].font_px == 19


def test_hug_text_blocks_stack_with_profile_gap(tmp_path: Path, monkeypatch) -> None:
    profile = fixture_profile(tmp_path / "profiles", hug_text=True)
    _patch_font(monkeypatch)
    card = _Overlay(blocks=(_TextBlock(lines=("Первый",)), _TextBlock(lines=("Второй",))))

    boxes = block_boxes(profile, card)

    gap = int(profile.style.gap * profile.height)
    assert boxes[1].top == boxes[0].top + boxes[0].height + gap
