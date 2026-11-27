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
_LINE_HEIGHT = 1.28
_BOX_RADIUS = 18
_HUG_PAD_X = 0.028
_HUG_PAD_Y = 0.42
_HUG_LINE_HEIGHT = 1.25
_HUG_RADIUS = 10
_CTA_DIVIDER_INSET = 14
_CTA_DIVIDER_GAP = 6
_CTA_DIVIDER_ALPHA = 60
_MUSIC_VOLUME = 0.35


@dataclass(frozen=True, slots=True)
class _TextBox:
    """One drawn card and its rectangle in the frame.

    ``lines`` are the main paragraph. A merged card keeps its call to action
    in ``cta_lines``; separate cards leave the CTA fields empty.
    """

    lines: tuple[str, ...]
    font_px: int
    line_px: int
    left: int
    top: int
    width: int
    height: int
    background: tuple[int, int, int, int]
    color: tuple[int, int, int]
    cta_lines: tuple[str, ...] = ()
    cta_font_px: int = 0
    cta_line_px: int = 0
    cta_color: tuple[int, int, int] | None = None
    divider: bool = False


def block_boxes(profile: _Profile, overlay: _Overlay) -> tuple[_TextBox, ...]:
    """Lay out the text card of the video.

    ``merge_blocks`` draws every block as one card with the last block as the
    CTA. Otherwise the first block sits at ``block1_y``; with ``hug_text``
    every next block follows the previous one with ``gap`` of empty frame
    space, and without it the next block sits at ``block2_y`` or ``block1_y +
    gap``. Hug, fixed-width and merged cards move up as a whole to stay in the
    frame and fail instead of drawing a cut card.

    Args:
        profile: Loaded profile with frame size and text style.
        overlay: Parsed card of the video.

    Returns:
        Boxes in block order; a merged card is the only box.
    """
    if profile.style.merge_blocks:
        card = _merged_card(profile, overlay)
        return _fit_stack((card,), profile.height) if card is not None else ()
    style = profile.style
    floor = _MIN_HUG_FONT_PX if style.hug_text else _FULL_WIDTH_MIN_FONT_PX
    font_px = max(floor, int(profile.width * style.font_size))
    max_text_width = _text_limit(profile)
    boxes: list[_TextBox] = []
    for index, block in enumerate(overlay.blocks):
        number = len(boxes) if style.hug_text else index
        is_cta = number > 0
        block_px = int(font_px * style.cta_scale) if is_cta else font_px
        line_px = (
            _cta_line_height(font_px, style.cta_scale) if is_cta else _line_height(style, font_px)
        )
        font = _load_font(profile.font, block_px)
        lines = _wrap(block.lines, font, max_text_width)
        if not lines:
            continue
        y_pct = style.block1_y if number == 0 else _second_y(style)
        box = _box(profile, style, number, lines, font, block_px, line_px, y_pct)
        if style.hug_text and boxes:
            box = _below(box, boxes[-1], int(style.gap * profile.height))
        boxes.append(box)
    stacked = style.hug_text or style.card_width is not None
    return _fit_stack(tuple(boxes), profile.height) if stacked else tuple(boxes)


def _merged_card(profile: _Profile, overlay: _Overlay) -> _TextBox | None:
    """Build the single card of ``merge_blocks``.

    All blocks but the last are one paragraph; the last block is the CTA,
    wrapped at its own scaled font and separated by the optional divider.
    """
    style = profile.style
    floor = _MIN_HUG_FONT_PX if style.hug_text else _FULL_WIDTH_MIN_FONT_PX
    font_px = max(floor, int(profile.width * style.font_size))
    font = _load_font(profile.font, font_px)
    text_width = _text_limit(profile)
    blocks = tuple(
        block.lines for block in overlay.blocks if any(line.strip() for line in block.lines)
    )
    if not blocks:
        return None
    cta_raw = blocks[-1] if len(blocks) > 1 else ()
    main_raw = tuple(line for lines in (blocks[:-1] if cta_raw else blocks) for line in lines)
    lines = _wrap(main_raw, font, text_width)
    cta_font_px = int(font_px * style.cta_scale)
    cta_font = _load_font(profile.font, cta_font_px)
    cta_lines = _wrap(cta_raw, cta_font, text_width)
    line_px = _line_height(style, font_px)
    cta_line_px = _cta_line_height(font_px, style.cta_scale)
    divider = bool(cta_lines and style.cta_divider)
    pad_y = font_px // 3
    height = line_px * len(lines) + cta_line_px * len(cta_lines) + 2 * pad_y
    if divider:
        height += 2 * _CTA_DIVIDER_GAP + 1
    width, left = _card_geometry(profile)
    background, color = _colors(style, 0)
    return _TextBox(
        lines=lines,
        font_px=font_px,
        line_px=line_px,
        left=left,
        top=_card_top(profile, height),
        width=width,
        height=height,
        background=background,
        color=color,
        cta_lines=cta_lines,
        cta_font_px=cta_font_px,
        cta_line_px=cta_line_px,
        cta_color=_colors(style, 1)[1] if cta_lines else None,
        divider=divider,
    )


def _text_limit(profile: _Profile) -> int:
    """Return the wrap width inside the card of the profile."""
    style = profile.style
    if style.hug_text:
        return int(profile.width * style.max_text_width)
    width, _ = _card_geometry(profile)
    if style.card_width is not None:
        return max(1, width - 2 * _pad_x(profile))
    return max(1, min(int(profile.width * style.max_text_width), width))


def _pad_x(profile: _Profile) -> int:
    """Horizontal text padding of a full or fixed-width card."""
    return int(profile.width * profile.style.text_pad_x)


def _card_geometry(profile: _Profile) -> tuple[int, int]:
    """Return the width and left edge of a full or fixed-width card."""
    if profile.style.card_width is not None:
        width = int(profile.width * profile.style.card_width)
    else:
        width = profile.width - 2 * _pad_x(profile)
    return width, (profile.width - width) // 2


def _card_top(profile: _Profile, height: int) -> int:
    """Centre a card at ``block1_y`` and keep it inside the frame."""
    top = int(profile.style.block1_y * profile.height - height / 2)
    return max(0, min(top, profile.height - height))


def _cta_line_height(font_px: int, scale: float) -> int:
    """Return the line height of the CTA at its scaled font."""
    return int(font_px * scale * _LINE_HEIGHT)


def _below(box: _TextBox, previous: _TextBox, gap: int) -> _TextBox:
    """Place *box* right below *previous*, keeping *gap* pixels between them."""
    return replace(box, top=previous.top + previous.height + gap)


def _fit_stack(boxes: tuple[_TextBox, ...], frame_height: int) -> tuple[_TextBox, ...]:
    """Move a card stack up so every card ends inside the frame.

    Raises:
        OverlayError: A card is taller than the frame or the stack cannot move
            up enough to fit every card.
    """
    if not boxes:
        return boxes
    bottom = max(box.top + box.height for box in boxes)
    if bottom <= frame_height:
        return boxes
    shift = bottom - frame_height
    shifted = tuple(replace(box, top=box.top - shift) for box in boxes)
    if any(box.top < 0 or box.top + box.height > frame_height for box in shifted):
        raise OverlayError(
            f"текст не помещается в кадр: нижняя кромка {bottom} при высоте {frame_height}"
        )
    return shifted


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
        overlay: Parsed card with one or more blocks.

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
        _draw_card_text(draw, profile, box)
    return image


def _draw_card_text(draw: ImageDraw.ImageDraw, profile: _Profile, box: _TextBox) -> None:
    """Draw the main paragraph, the CTA divider and the CTA of one card."""
    main_h = box.line_px * len(box.lines)
    cta_h = box.cta_line_px * len(box.cta_lines)
    divider_h = 2 * _CTA_DIVIDER_GAP + 1 if box.divider else 0
    y = box.top + (box.height - main_h - divider_h - cta_h) // 2
    font = _load_font(profile.font, box.font_px)
    _draw_lines(draw, box.lines, font, box.color, box.left, y, box.width, box.line_px)
    y += main_h
    if box.divider:
        y += _CTA_DIVIDER_GAP
        draw.line(
            [
                (box.left + _CTA_DIVIDER_INSET, y),
                (box.left + box.width - _CTA_DIVIDER_INSET, y),
            ],
            fill=(*_rgb(profile.style.text_color), _CTA_DIVIDER_ALPHA),
            width=1,
        )
        y += 1 + _CTA_DIVIDER_GAP
    if box.cta_lines:
        font = _load_font(profile.font, box.cta_font_px)
        _draw_lines(
            draw,
            box.cta_lines,
            font,
            box.cta_color or box.color,
            box.left,
            y,
            box.width,
            box.cta_line_px,
        )


def _draw_lines(
    draw: ImageDraw.ImageDraw,
    lines: tuple[str, ...],
    font: ImageFont.FreeTypeFont,
    color: tuple[int, int, int],
    left: int,
    top: int,
    width: int,
    line_px: int,
) -> None:
    """Draw centered lines of one part of a card."""
    y = top
    for line in lines:
        text_width = draw.textlength(line, font=font)
        draw.text((left + (width - text_width) / 2, y), line, font=font, fill=color)
        y += line_px


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
    temporary = output.with_name(f".{output.stem}.tmp{output.suffix}")
    try:
        try:
            render_card(profile, overlay).save(card_path)
        except OSError as ex:
            raise OverlayError(f"не удалось подготовить карточку {card_path}: {ex}") from ex
        media.run(_command(source, card_path, temporary, audio))
        if temporary.is_file():
            temporary.replace(output)
    finally:
        card_path.unlink(missing_ok=True)
        temporary.unlink(missing_ok=True)


def _box(
    profile: _Profile,
    style: _OverlayStyle,
    index: int,
    lines: tuple[str, ...],
    font: ImageFont.FreeTypeFont,
    font_px: int,
    line_px: int,
    y_pct: float,
) -> _TextBox:
    """Build the rectangle and colours of one block.

    Profiles with ``hug_text`` get a card as wide as the text, ``card_width``
    gets a fixed centered card and the default keeps the full-width card of
    the older profiles.
    """
    background, color = _colors(style, index)
    if style.hug_text:
        pad_x = max(10, int(profile.width * _HUG_PAD_X))
        pad_y = max(7, int(font_px * _HUG_PAD_Y))
        width = max(int(font.getlength(line)) for line in lines) + 2 * pad_x
        width = min(width, profile.width)
        left = max(0, (profile.width - width) // 2)
    else:
        pad_y = font_px // 3
        width, left = _card_geometry(profile)
    height = line_px * len(lines) + 2 * pad_y
    top = int(y_pct * profile.height - height / 2)
    top = max(0, min(top, profile.height - height))
    return _TextBox(
        lines=lines,
        font_px=font_px,
        line_px=line_px,
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
) -> tuple[str, ...]:
    """Wrap the paragraph of one block by words.

    The model lines of a block are one paragraph, so they are joined before
    wrapping. A word wider than the limit is split by characters.
    """
    words = " ".join(lines).split()
    wrapped: list[str] = []
    current = ""
    for word in words:
        if font.getlength(word) > max_width:
            if current:
                wrapped.append(current)
                current = ""
            wrapped.extend(_chunks(word, font, max_width))
        elif not current:
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
