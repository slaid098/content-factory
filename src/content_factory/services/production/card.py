"""Producer of photo posts: profile text rendered onto an opaque card."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from loguru import logger
from PIL import Image

from content_factory.config.settings import GeneratorSettings
from content_factory.constants import PROFILES_DIR
from content_factory.exceptions.generator import GeneratorError, OverlayError, QaError
from content_factory.schemas.content.delivery import ContentType
from content_factory.schemas.generator.run import (
    ProducedBatch,
    ProductionStep,
    ProfileRef,
    RunItem,
    RunReport,
    RunStatus,
    RunStep,
    TextWriter,
)
from content_factory.services.generator import (
    TextClient,
    choose_run_id,
    generate_overlay,
    load_profile,
    profile_slug,
    publish_photo,
    render_card,
    validate_count,
    write_report,
)
from content_factory.services.production import qa as production_qa
from content_factory.services.production.registry import CARD_PRODUCER
from content_factory.services.production.reporting import close_report, find_step, mark_failed

_FINAL_DIR = "final"
_JPEG_QUALITY = 90
_MAX_CAPTION_BYTES = 4096


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

    supported_types = frozenset({ContentType.PHOTO})

    def __init__(
        self,
        *,
        writer: TextWriter | None = None,
        renderer: _Renderer | None = None,
        now: datetime | None = None,
    ) -> None:
        """Store optional test seams; production uses the text model and PIL.

        Args:
            writer: Text model port; defaults to :class:`TextClient`.
            renderer: Card renderer; defaults to ``overlay.render_card``.
            now: Run start moment, fixed in tests.
        """
        self._writer, self._renderer, self._now = writer, renderer, now

    def produce(
        self,
        profile: ProfileRef,
        count: int,
        data_dir: Path,
        settings: GeneratorSettings,
    ) -> ProducedBatch:
        """Produce *count* photo posts of one profile and publish complete pairs.

        Args:
            profile: Profile whose card is rendered; only its name is used.
            count: Number of posts, at least one.
            data_dir: Root ``app_data`` directory with ``profiles`` and stock.
            settings: Generator settings with the output directory.

        Returns:
            Run id, counters and the run directory of the step.

        Raises:
            GeneratorError: ``count`` is invalid.
        """
        validate_count(count)
        loaded = load_profile(profile.name, data_dir / PROFILES_DIR)
        step = find_step(loaded.production, CARD_PRODUCER, ContentType.PHOTO)
        started = self._now or datetime.now(UTC)
        run_id = choose_run_id(settings.output_dir, [loaded], started)
        run_dir = settings.output_dir / loaded.language / profile_slug(loaded) / run_id
        report = _report(
            loaded.name,
            profile_slug(loaded),
            loaded.production,
            step,
            count,
            run_id,
            started,
        )
        write_report(run_dir, report)
        writer = self._writer or TextClient(settings)
        renderer = self._renderer or render_card
        final_dir = run_dir / _FINAL_DIR
        generated = failed = total_bytes = 0
        for item in report.items:
            try:
                size = _produce_item(loaded, step, item, writer, renderer, final_dir, data_dir)
            except (
                GeneratorError,
                OSError,
                UnicodeError,
                Image.DecompressionBombError,
                MemoryError,
                OverflowError,
            ) as ex:
                failed += 1
                mark_failed(item, ex, "Фото не собрано")
            else:
                generated += 1
                total_bytes += size
                item.status = RunStatus.DONE
                logger.bind(file=item.file).info("Фото готово")
            write_report(run_dir, report)
        close_report(report, generated, failed, total_bytes)
        write_report(run_dir, report)
        return ProducedBatch(
            run_id=run_id,
            generated=report.totals.generated,
            failed=report.totals.failed,
            run_dirs=(run_dir,),
        )


def _produce_item(
    profile: Any,
    step: ProductionStep,
    item: RunItem,
    writer: TextWriter,
    renderer: _Renderer,
    final_dir: Path,
    data_dir: Path,
) -> int:
    """Generate, check and publish one photo; return its JPEG size.

    Raises:
        QaError: A declared final check rejected the photo.
        GeneratorError: The caption or the publication failed.
        OSError: A file could not be read or written.
        UnicodeError: The caption cannot be encoded as UTF-8.
        MemoryError: The image does not fit into memory.
        OverflowError: The image size overflows the renderer.
        Image.DecompressionBombError: The JPEG declares too many pixels.
    """
    final = final_dir / item.file
    overlay = generate_overlay(profile, writer)
    caption = _checked_caption(overlay)
    _render_jpeg(profile, overlay, renderer, final)
    _write_sidecar(final, caption)
    reason = _qa_reason(step, final, profile.width, profile.height)
    if reason is not None:
        raise QaError(reason)
    size = final.stat().st_size
    publish_photo(profile, final, data_dir)
    return size


def _report(
    name: str,
    slug: str,
    production: tuple[ProductionStep, ...],
    step: ProductionStep,
    count: int,
    run_id: str,
    started: datetime,
) -> RunReport:
    """Build the initial ``run.json`` report of one card run."""
    items = [
        RunItem(
            file=f"{slug}_{run_id}_{seq:03d}.jpg",
            profile=name,
            producer=step.producer,
            type=step.type,
            qa=list(step.qa),
        )
        for seq in range(1, count + 1)
    ]
    return RunReport(
        run_id=run_id,
        started_at=started,
        status=RunStatus.RUNNING,
        profiles=[name],
        count=count,
        production=[
            RunStep(producer=entry.producer, type=entry.type, qa=list(entry.qa))
            for entry in production
        ],
        items=items,
    )


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


def _qa_reason(
    step: ProductionStep,
    final: Path,
    width: int,
    height: int,
) -> str | None:
    """Run the mandatory image check and then the declared final checks."""
    names = ("image", *(name for name in step.qa if name != "image"))
    handlers: Mapping[str, production_qa.QaHandler] = {
        "image": lambda: check_image(final, width, height)
    }
    return production_qa.run(names, production_qa.QA_FINAL, handlers)


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
