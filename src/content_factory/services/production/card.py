"""Layer producer of photo posts: profile text rendered onto an opaque card."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from loguru import logger
from PIL import Image

from content_factory.constants import PROFILES_DIR
from content_factory.exceptions.generator import GeneratorError, OverlayError, QaError
from content_factory.schemas.generator.pipeline import (
    Artifact,
    ArtifactKind,
    Layer,
    PipelineContext,
)
from content_factory.schemas.generator.text import TextWriter
from content_factory.services.generator import (
    TextClient,
    generate_overlay,
    load_profile,
    profile_slug,
    publish_photo,
    render_card,
    validate_count,
)
from content_factory.services.production.paths import context_run_dir
from content_factory.utils.run_id import validate_run_id

_FINAL_DIR = "final"
_JPEG_QUALITY = 90
_MAX_CAPTION_BYTES = 4096
_JPEG_MEDIA_TYPE = "image/jpeg"


class _Style(Protocol):
    """Text card settings the photo background needs."""

    @property
    def text_bg_color(self) -> str:
        """Opaque background colour of the text card."""
        ...


class _CardProfile(Protocol):
    """Profile fields the card renderer reads."""

    @property
    def width(self) -> int:
        """Width of the photo in pixels."""
        ...

    @property
    def height(self) -> int:
        """Height of the photo in pixels."""
        ...

    @property
    def style(self) -> _Style:
        """Text card style of the profile."""
        ...


class _TextBlock(Protocol):
    """One visual block of a card."""

    @property
    def lines(self) -> tuple[str, ...]:
        """Lines of the block."""
        ...


class _CardText(Protocol):
    """Parsed text card of one post."""

    @property
    def blocks(self) -> tuple[_TextBlock, ...]:
        """Blocks in drawing order."""
        ...


_Renderer = Callable[..., Image.Image]


class CardProducer:
    """Build photo posts from the profile caption and its rendered card."""

    supported_outputs = frozenset({ArtifactKind.PHOTO})

    def __init__(
        self,
        *,
        writer: TextWriter | None = None,
        renderer: _Renderer | None = None,
    ) -> None:
        """Store optional test seams; production uses the text model and PIL.

        Args:
            writer: Text model port; defaults to :class:`TextClient`.
            renderer: Card renderer; defaults to ``overlay.render_card``.
        """
        self._writer, self._renderer = writer, renderer

    def plan_layer(self, layer: Layer, context: PipelineContext) -> tuple[Artifact, ...]:
        """Plan photo post names of every profile of the group."""
        del layer
        artifacts: list[Artifact] = []
        for profile in context.profiles:
            loaded = load_profile(profile.name, context.data_dir / PROFILES_DIR)
            run_dir = context_run_dir(context, loaded.language, profile_slug(loaded))
            for seq in range(1, context.count + 1):
                filename = _post_name(loaded.name, context.run_id, seq)
                artifacts.append(
                    Artifact(
                        item=filename,
                        profile=loaded.name,
                        kind=ArtifactKind.PHOTO,
                        path=run_dir / _FINAL_DIR / filename,
                        media_type=_JPEG_MEDIA_TYPE,
                        meta=_plan_meta(loaded),
                    )
                )
        return tuple(artifacts)

    def produce_layer(
        self,
        layer: Layer,
        inputs: tuple[Artifact, ...],
        context: PipelineContext,
    ) -> tuple[Artifact, ...]:
        """Produce *count* photo posts of every profile and publish pairs."""
        del inputs
        validate_count(context.count)
        writer = self._writer or TextClient(context.settings)
        renderer = self._renderer or render_card
        artifacts: list[Artifact] = []
        for profile in context.profiles:
            loaded = load_profile(profile.name, context.data_dir / PROFILES_DIR)
            run_dir = context_run_dir(context, loaded.language, profile_slug(loaded))
            for seq in range(1, context.count + 1):
                filename = _post_name(loaded.name, context.run_id, seq)
                artifacts.append(
                    self._produce_item(
                        loaded,
                        filename,
                        run_dir / _FINAL_DIR,
                        writer,
                        renderer,
                        context,
                    )
                )
        return tuple(artifacts)

    def _produce_item(
        self,
        profile: Any,
        filename: str,
        final_dir: Path,
        writer: TextWriter,
        renderer: _Renderer,
        context: PipelineContext,
    ) -> Artifact:
        """Produce, check and publish one photo; failures stay on the artifact."""
        final = final_dir / filename
        try:
            size = _produce_item(profile, writer, renderer, final, context)
        except (
            GeneratorError,
            OSError,
            UnicodeError,
            Image.DecompressionBombError,
            MemoryError,
            OverflowError,
        ) as ex:
            logger.bind(file=filename, error=str(ex), error_type=type(ex).__name__).error(
                "Фото не собрано"
            )
            return _artifact(profile, filename, final, "failed", str(ex))
        logger.bind(file=filename).info("Фото готово")
        return _artifact(profile, filename, final, "done", None, size)


def _produce_item(
    profile: Any,
    writer: TextWriter,
    renderer: _Renderer,
    final: Path,
    context: PipelineContext,
) -> int:
    """Generate, check and publish one photo; return its JPEG size.

    Raises:
        QaError: The mandatory image check rejected the photo.
        GeneratorError: The caption or the publication failed.
        OSError: A file could not be read or written.
        UnicodeError: The caption cannot be encoded as UTF-8.
        MemoryError: The image does not fit into memory.
        OverflowError: The image size overflows the renderer.
        Image.DecompressionBombError: The JPEG declares too many pixels.
    """
    overlay = generate_overlay(profile, writer)
    caption = _checked_caption(overlay)
    _render_jpeg(profile, overlay, renderer, final)
    _write_sidecar(final, caption)
    reason = check_image(final, profile.width, profile.height)
    if reason is not None:
        raise QaError(reason)
    size = final.stat().st_size
    publish_photo(profile, final, context.data_dir)
    return size


def _artifact(
    profile: Any,
    filename: str,
    final: Path,
    status: str,
    error: str | None,
    size: int = 0,
) -> Artifact:
    """Describe one produced photo as a layer artifact."""
    return Artifact(
        item=filename,
        profile=profile.name,
        kind=ArtifactKind.PHOTO,
        path=final,
        media_type=_JPEG_MEDIA_TYPE,
        meta={
            "look_id": None,
            "seed": None,
            "frames": None,
            "width": profile.width,
            "height": profile.height,
            "status": status,
            "error": error,
            "bytes": size,
        },
    )


def _plan_meta(profile: Any) -> dict[str, object]:
    """Describe the plan of one photo post."""
    return {
        "look_id": None,
        "seed": None,
        "frames": None,
        "width": profile.width,
        "height": profile.height,
        "status": "planned",
    }


def _post_name(name: str, run_id: str, seq: int) -> str:
    """Return the file name of one photo post.

    Raises:
        GeneratorError: The run id cannot be used in a file name.
    """
    validate_run_id(run_id)
    return f"{name.replace('/', '_')}_{run_id}_{seq:03d}.jpg"


def _render_jpeg(
    profile: _CardProfile,
    overlay: _CardText,
    renderer: _Renderer,
    target: Path,
) -> None:
    """Draw the card over an opaque background and save the RGB JPEG.

    Raises:
        OverlayError: The profile colour or the card renderer failed.
        OSError: The JPEG cannot be written.
        MemoryError: The image does not fit into memory.
        OverflowError: The image size overflows the renderer.
    """
    try:
        image = Image.new("RGB", (profile.width, profile.height), profile.style.text_bg_color)
    except (ValueError, OSError) as ex:
        raise OverlayError(f"неверный цвет фона {profile.style.text_bg_color!r}: {ex}") from ex
    try:
        card = renderer(profile, overlay)
        if card.mode != "RGBA":
            card = card.convert("RGBA")
        image.paste(card, (0, 0), card)
    except (OSError, ValueError) as ex:
        raise OverlayError(f"не удалось нарисовать карточку: {ex}") from ex
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        image.save(target, format="JPEG", quality=_JPEG_QUALITY)
    except OSError as ex:
        raise OverlayError(f"не удалось сохранить JPEG {target}: {ex}") from ex


def _caption(overlay: _CardText) -> str:
    """Join every line of every block into the post text."""
    return "\n".join(line for block in overlay.blocks for line in block.lines)


def _checked_caption(overlay: _CardText) -> str:
    """Return the caption only when it is valid UTF-8 within the size limit.

    Raises:
        GeneratorError: The caption contains a lone surrogate or is longer
            than ``_MAX_CAPTION_BYTES`` UTF-8 bytes.
    """
    caption = _caption(overlay)
    try:
        size = len(caption.encode("utf-8"))
    except UnicodeEncodeError as ex:
        raise GeneratorError("текст поста не кодируется в UTF-8") from ex
    if size > _MAX_CAPTION_BYTES:
        raise GeneratorError(f"текст поста больше {_MAX_CAPTION_BYTES} байт UTF-8: {size}")
    return caption


def _write_sidecar(final: Path, caption: str) -> None:
    """Serialize and encode ``{"text": caption}`` before opening the file.

    Raises:
        GeneratorError: The caption cannot be encoded as UTF-8.
    """
    try:
        payload = (json.dumps({"text": caption}, ensure_ascii=False, indent=2) + "\n").encode(
            "utf-8"
        )
    except UnicodeEncodeError as ex:
        raise GeneratorError("текст поста не кодируется в UTF-8") from ex
    final.with_suffix(".json").write_bytes(payload)


def check_image(path: Path, width: int, height: int) -> str | None:
    """Return a defect reason when the photo is unreadable or has another size.

    Args:
        path: Finished JPEG of one post.
        width: Expected width of the profile.
        height: Expected height of the profile.

    Returns:
        ``None`` for a non-empty readable JPEG of the profile size, otherwise
        a user-facing reason.
    """
    try:
        if path.stat().st_size <= 0:
            return f"файл пуст: {path}"
        with Image.open(path) as image:
            if image.format != "JPEG":
                return f"не JPEG ({image.format}): {path}"
            image.load()
            size = image.size
    except Image.DecompressionBombError:
        return f"слишком большое изображение: {path}"
    except (OSError, ValueError):
        return f"не удалось открыть JPEG: {path}"
    if size != (width, height):
        return f"размер {size[0]}x{size[1]} не совпадает с профилем {width}x{height}: {path}"
    return None


__all__ = ["CardProducer", "check_image"]
