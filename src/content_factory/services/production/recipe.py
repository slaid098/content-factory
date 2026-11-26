"""Reading and validation of the ``production`` block of a profile recipe.

The registry and the generator share this module: the registry only needs the
step types to know what a profile can produce, while the generator builds the
layers themselves. Both must reject the same broken recipe, so the check lives
next to the producer and QA registries it consults.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from content_factory.exceptions.generator import ProfileError
from content_factory.schemas.content.delivery import ContentType
from content_factory.schemas.generator.pipeline import ArtifactKind, Layer, artifact_kind
from content_factory.services.production import qa, registry

DEFAULT_RECIPE = (
    Layer(
        name=registry.VAST_PRODUCER,
        producer=registry.VAST_PRODUCER,
        inputs=(),
        output=ArtifactKind.VIDEO,
        checks=("clef", "mp4"),
    ),
)
"""Recipe of a profile without a ``production`` block."""

_DEFAULT_QA = ("clef", "mp4")
_PHOTO_QA = ("image",)
_VIDEO_ONLY_QA = frozenset({"clef", "mp4"})
_PHOTO_ONLY_QA = frozenset({"image"})


def read_recipe(data: Mapping[str, Any], name: str) -> tuple[Layer, ...]:
    """Validate the ``production`` block and build its layers.

    A missing block keeps the legacy video recipe. Every step is checked
    against the process registries before a batch is planned: the producer is
    registered, it supports the step type, and every QA check exists and fits
    that type. The registry relies on the same call, so a broken recipe never
    reaches either the refill decision or the generator.

    Args:
        data: Parsed ``profile.yaml``.
        name: Profile id used in error messages.

    Returns:
        One layer per step, in declaration order.

    Raises:
        ProfileError: The block is empty or not a list, a step is not a
            mapping, repeats a producer, names an unknown producer or type,
            or a QA check is unknown, repeated or unfit for the step type.
    """
    raw = data.get("production")
    if raw is None:
        return DEFAULT_RECIPE
    if not isinstance(raw, list) or not raw:
        raise ProfileError(f"в профиле {name} production должен быть непустым списком")
    allowed_producers = registry.available()
    allowed_qa = qa.available_checks()
    layers: list[Layer] = []
    seen: set[str] = set()
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise ProfileError(f"шаг production {index} в профиле {name} должен быть словарём")
        producer = str(item.get("producer") or "").strip()
        if not producer:
            raise ProfileError(f"у шага production {index} в профиле {name} нет producer")
        if producer in seen:
            raise ProfileError(f"повтор producer {producer!r} в production профиля {name}")
        if producer not in allowed_producers:
            allowed = ", ".join(allowed_producers)
            raise ProfileError(
                f"неизвестный producer {producer!r} в профиле {name}: допустимы {allowed}"
            )
        engine = registry.resolve(producer)
        content_type = _content_type(item.get("type"), name)
        output = artifact_kind(content_type)
        if output not in engine.supported_outputs:
            supported = ", ".join(sorted(kind.value for kind in engine.supported_outputs))
            raise ProfileError(
                f"producer {producer!r} в профиле {name} не производит "
                f"type={content_type.value!r}: поддержаны {supported}"
            )
        seen.add(producer)
        layers.append(
            Layer(
                name=producer,
                producer=producer,
                inputs=(),
                output=output,
                checks=_qa_names(item.get("qa"), name, index, allowed_qa, content_type),
            )
        )
    return tuple(layers)


def _content_type(raw: object, name: str) -> ContentType:
    """Parse a step type; an absent value keeps the old video default."""
    if raw is None:
        return ContentType.VIDEO
    try:
        return ContentType(str(raw).strip().lower())
    except ValueError as ex:
        allowed = ", ".join(item.value for item in ContentType)
        raise ProfileError(
            f"неизвестный type {raw!r} в профиле {name}: допустимы {allowed}"
        ) from ex


def _qa_names(
    raw: object,
    name: str,
    index: int,
    allowed: tuple[str, ...],
    content_type: ContentType,
) -> tuple[str, ...]:
    """Read the ordered QA names of one step, rejecting blanks and repeats."""
    if raw is None:
        return _PHOTO_QA if content_type is ContentType.PHOTO else _DEFAULT_QA
    if not isinstance(raw, list) or not raw:
        raise ProfileError(
            f"qa шага production {index} в профиле {name} должен быть непустым списком"
        )
    names: list[str] = []
    incompatible = _VIDEO_ONLY_QA if content_type is ContentType.PHOTO else _PHOTO_ONLY_QA
    for value in raw:
        qa_name = str(value).strip()
        if not qa_name:
            raise ProfileError(f"пустое имя QA в профиле {name}")
        if qa_name in names:
            raise ProfileError(f"повтор QA {qa_name!r} в профиле {name}")
        if qa_name not in allowed:
            raise ProfileError(
                f"неизвестная QA-проверка {qa_name!r} в профиле {name}: "
                f"допустимы {', '.join(allowed)}"
            )
        if qa_name in incompatible:
            raise ProfileError(
                f"QA {qa_name!r} не подходит для type={content_type.value!r} в профиле {name}"
            )
        names.append(qa_name)
    return tuple(names)


__all__ = ["DEFAULT_RECIPE", "read_recipe"]
