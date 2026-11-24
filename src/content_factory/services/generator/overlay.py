"""Text card rendering and the final ffmpeg cut with profile music."""

from __future__ import annotations

from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from content_factory.exceptions.generator import OverlayError
from content_factory.services.generator import media
from content_factory.services.generator.profiles import _OverlayStyle, _Profile
from content_factory.services.generator.texts import _Overlay

_MIN_HUG_FONT_PX = 12
_FULL_WIDTH_MIN_FONT_PX = 20
_HORIZONTAL_PAD = 0.04
_LINE_HEIGHT = 1.28
_BOX_RADIUS = 18
_HUG_PAD_X = 0.028
_HUG_PAD_Y = 0.42
_HUG_LINE_HEIGHT = 1.25
_HUG_RADIUS = 10
_MUSIC_VOLUME = 0.35


@dataclass(frozen=True, slots=True)
class _TextBox:
    """One drawn text block and its rectangle in the frame."""

    lines: tuple[str, ...]
    font_px: int
    left: int
    top: int
    width: int
    height: int
    background: tuple[int, int, int, int]
    color: tuple[int, int, int]


def block_boxes(profile: _Profile, overlay: _Overlay) -> tuple[_TextBox, ...]:
    """Lay out one or two text blocks inside the video frame.

    The first block sits at ``block1_y``. With ``hug_text`` every next block
    follows the previous one with ``gap`` of empty frame space; otherwise the
    next block sits at ``block2_y``. The second block receives the accent
    colours of the profile. A hug stack that does not fit moves up as a whole;
    a stack taller than the frame fails instead of drawing a cut card.

    Args:
        profile: Loaded profile with frame size and text style.
        overlay: Parsed card of the video.

    Returns:
        Boxes in block order.
    """
    style = profile.style
    floor = _MIN_HUG_FONT_PX if style.hug_text else _FULL_WIDTH_MIN_FONT_PX
    font_px = max(floor, int(profile.width * style.font_size))
    font = _load_font(profile.font, font_px)
    max_text_width = int(profile.width * style.max_text_width)
    boxes: list[_TextBox] = []
    for index, block in enumerate(overlay.blocks):
        lines = _wrap(block.lines, font, max_text_width, break_words=style.hug_text)
        if not lines:
            continue
        y_pct = style.block1_y if index == 0 else _second_y(style)
        box = _box(profile, style, index, lines, font, font_px, y_pct)
        if style.hug_text and boxes:
            box = _below(box, boxes[-1], int(style.gap * profile.height))
        boxes.append(box)
    return _fit_stack(tuple(boxes), profile.height) if style.hug_text else tuple(boxes)


def _below(box: _TextBox, previous: _TextBox, gap: int) -> _TextBox:
    """Place *box* right below *previous*, keeping *gap* pixels between them."""
    return replace(box, top=previous.top + previous.height + gap)


def _fit_stack(boxes: tuple[_TextBox, ...], frame_height: int) -> tuple[_TextBox, ...]:
    """Move a hug stack up so its last card ends inside the frame.

    Raises:
        OverlayError: The stack is taller than the frame and cannot fit.
    """
    if not boxes:
        return boxes
    bottom = boxes[-1].top + boxes[-1].height
    if bottom <= frame_height:
        return boxes
    shift = bottom - frame_height
    if boxes[0].top < shift:
        raise OverlayError(
            f"текст не помещается в кадр: нижняя кромка {bottom} при высоте {frame_height}"
        )
    return tuple(replace(box, top=box.top - shift) for box in boxes)


def _second_y(style: _OverlayStyle) -> float:
    """Return the Y centre of the second block.

    Profiles may set ``block2_y`` explicitly or only ``gap`` below the first
    block; the latter is what the single-card street profile uses.
    """
    if style.block2_y is not None:
        return style.block2_y
    return style.block1_y + style.gap


def render_card(profile: _Profile, overlay: _Overlay) -> Image.Image:
    """Draw a transparent PNG card of the video size.

    Args:
        profile: Loaded profile.
        overlay: Parsed card with one or two blocks.

    Returns:
        RGBA image of ``profile.width x profile.height``.
    """
    image = Image.new("RGBA", (profile.width, profile.height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = _HUG_RADIUS if profile.style.hug_text else _BOX_RADIUS
    for box in block_boxes(profile, overlay):
        draw.rounded_rectangle(
            [box.left, box.top, box.left + box.width, box.top + box.height],
            radius=radius,
            fill=box.background,
        )
        font = _load_font(profile.font, box.font_px)
        line_h = _line_height(profile.style, box.font_px)
        text_h = line_h * len(box.lines)
        y = box.top + (box.height - text_h) // 2
        for line in box.lines:
            width = draw.textlength(line, font=font)
            draw.text((box.left + (box.width - width) / 2, y), line, font=font, fill=box.color)
            y += line_h
    return image


def mix(
    profile: _Profile,
    overlay: _Overlay,
    source: Path,
    output: Path,
    audio: Path | None,
) -> None:
    """_Overlay the text card and replace the model audio with profile music.

    Args:
        profile: Loaded profile.
        overlay: Parsed card of the video.
        source: Silent mp4 downloaded from the server.
        output: Final mp4 in the ``final/`` directory.
        audio: Music track, or ``None`` for a silent final.

    Raises:
        OverlayError: The card or ffmpeg cannot be written or ffmpeg fails.
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    card_path = output.with_name(f"{output.stem}.card.png")
    try:
        render_card(profile, overlay).save(card_path)
        media.run(_command(source, card_path, output, audio))
    except OSError as ex:
        raise OverlayError(f"не удалось подготовить карточку {card_path}: {ex}") from ex
    finally:
        card_path.unlink(missing_ok=True)


def _box(
    profile: _Profile,
    style: _OverlayStyle,
    index: int,
    lines: tuple[str, ...],
    font: ImageFont.FreeTypeFont,
    font_px: int,
    y_pct: float,
) -> _TextBox:
    """Build the rectangle and colours of one block.

    Profiles with ``hug_text`` get a card as wide as the text; the default
    keeps the full-width card of the older profiles.
    """
    background, color = _colors(style, index)
    if style.hug_text:
        pad_x = max(10, int(profile.width * _HUG_PAD_X))
        pad_y = max(7, int(font_px * _HUG_PAD_Y))
        line_h = _line_height(style, font_px)
        width = max(int(font.getlength(line)) for line in lines) + 2 * pad_x
        width = min(width, profile.width)
        left = max(0, (profile.width - width) // 2)
    else:
        pad_x = int(profile.width * _HORIZONTAL_PAD)
        pad_y = font_px // 3
        line_h = _line_height(style, font_px)
        width = profile.width - 2 * pad_x
        left = pad_x
    height = line_h * len(lines) + 2 * pad_y
    top = int(y_pct * profile.height - height / 2)
    top = max(0, min(top, profile.height - height))
    return _TextBox(
        lines=lines,
        font_px=font_px,
        left=left,
        top=top,
        width=width,
        height=height,
        background=background,
        color=color,
    )


def _line_height(style: _OverlayStyle, font_px: int) -> int:
    """Return the line height of the layout the profile uses."""
    ratio = _HUG_LINE_HEIGHT if style.hug_text else _LINE_HEIGHT
    return int(font_px * ratio)


def _colors(
    style: _OverlayStyle, index: int
) -> tuple[tuple[int, int, int, int], tuple[int, int, int]]:
    """Return the background and text colours of one block."""
    background = _rgba(style.text_bg_color, style.text_bg_alpha)
    color = _rgb(style.text_color)
    if index > 0:
        alpha = style.text_bg_alpha if style.block2_alpha is None else style.block2_alpha
        background = _rgba(style.block2_bg or style.text_bg_color, alpha)
        color = _rgb(style.block2_text or style.text_color)
    return background, color


def _command(source: Path, card: Path, output: Path, audio: Path | None) -> list[str]:
    """Build the ffmpeg command of the final cut."""
    command = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-i",
        str(source),
        "-i",
        str(card),
    ]
    if audio is None:
        command += [
            "-filter_complex",
            "[0:v][1:v]overlay=0:0:format=auto,format=yuv420p[v]",
            "-map",
            "[v]",
            "-an",
        ]
    else:
        command += ["-stream_loop", "-1", "-i", str(audio)]
        command += [
            "-filter_complex",
            (
                "[0:v][1:v]overlay=0:0:format=auto,format=yuv420p[v];"
                f"[2:a]volume={_MUSIC_VOLUME},afade=t=in:st=0:d=0.5[a]"
            ),
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
        ]
    command += [
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-movflags",
        "+faststart",
        str(output),
    ]
    return command


def _wrap(
    lines: tuple[str, ...],
    font: ImageFont.FreeTypeFont,
    max_width: int,
    *,
    break_words: bool = False,
) -> tuple[str, ...]:
    """Wrap card lines by words so they fit the frame width."""
    wrapped: list[str] = []
    for line in lines:
        words = line.split()
        if not words:
            continue
        current = ""
        for word in words:
            if break_words and font.getlength(word) > max_width:
                if current:
                    wrapped.append(current)
                    current = ""
                wrapped.extend(_chunks(word, font, max_width))
                continue
            if not current:
                current = word
            elif font.getlength(f"{current} {word}") <= max_width:
                current = f"{current} {word}"
            else:
                wrapped.append(current)
                current = word
        if current:
            wrapped.append(current)
    return tuple(wrapped)


def _chunks(word: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    """Split one overlong word into pieces that fit *max_width*."""
    chunks: list[str] = []
    chunk = ""
    for char in word:
        candidate = chunk + char
        if chunk and font.getlength(candidate) > max_width:
            chunks.append(chunk)
            chunk = char
        else:
            chunk = candidate
    if chunk:
        chunks.append(chunk)
    return chunks


@lru_cache(maxsize=8)
def _load_font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    """Load the profile font; tests replace this seam."""
    try:
        return ImageFont.truetype(str(path), size)
    except OSError as ex:
        raise OverlayError(f"не удалось открыть шрифт {path}: {ex}") from ex


def _rgb(value: str) -> tuple[int, int, int]:
    """Convert ``#RRGGBB`` to an RGB tuple."""
    cleaned = value.lstrip("#")
    try:
        return (int(cleaned[0:2], 16), int(cleaned[2:4], 16), int(cleaned[4:6], 16))
    except (IndexError, ValueError) as ex:
        raise OverlayError(f"неверный цвет {value!r} в профиле") from ex


def _rgba(value: str, alpha: int) -> tuple[int, int, int, int]:
    """Convert ``#RRGGBB`` and alpha to an RGBA tuple."""
    return (*_rgb(value), max(0, min(255, alpha)))
