"""Layer adapters of the existing media, Clef and image checks.

A producer keeps rejecting an item before publication, so these adapters are
the pipeline gate on the produced artifact: a producer that could not build an
item returns an artifact marked ``failed`` with the original reason, which the
first declared check reports as an ``ItemFailure``.
"""

from __future__ import annotations

from collections.abc import Callable

from content_factory.exceptions.generator import QaError
from content_factory.schemas.generator.pipeline import (
    Artifact,
    CheckResult,
    Layer,
    PipelineContext,
)

_Verifier = Callable[[Artifact], str | None]


class ArtifactCheck:
    """Layer check that rejects a failed artifact, then runs its verifier."""

    def __init__(self, verify: _Verifier) -> None:
        """Store the verifier of one produced artifact kind."""
        self._verify = verify

    def check(
        self,
        layer: Layer,
        artifact: Artifact,
        context: PipelineContext,
    ) -> CheckResult:
        """Reject a failed item, otherwise return the verifier verdict."""
        del layer, context
        failed = _produced_failure(artifact)
        if failed is not None:
            return failed
        reason = self._verify(artifact)
        return CheckResult(reason is None, reason)


def media_reason(artifact: Artifact) -> str | None:
    """Probe the final mp4 and return the first defect reason."""
    seconds = _number(artifact.meta.get("expected_seconds"))
    if seconds is None:
        return f"у ролика {artifact.item} нет ожидаемой длительности"
    from content_factory.services.generator import check_video

    try:
        check_video(
            artifact.path,
            seconds,
            bool(artifact.meta.get("expect_audio")),
        )
    except QaError as ex:
        return str(ex)
    return None


def source_reason(artifact: Artifact) -> str | None:
    """Accept a produced source: the generator has already checked it with Clef."""
    del artifact
    return None


def image_reason(artifact: Artifact) -> str | None:
    """Open the finished JPEG and return the first defect reason."""
    width = _integer(artifact.meta.get("width"))
    height = _integer(artifact.meta.get("height"))
    if width is None or height is None:
        return f"у фото {artifact.item} нет размера профиля"
    from content_factory.services.production.card import check_image

    return check_image(artifact.path, width, height)


def _produced_failure(artifact: Artifact) -> CheckResult | None:
    """Return the producer failure of a marked artifact, if any."""
    if artifact.meta.get("status") != "failed":
        return None
    reason = artifact.meta.get("error")
    return CheckResult(False, str(reason) if reason else f"item {artifact.item} не собран")


def _number(value: object) -> float | None:
    """Return a finite positive number or ``None``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if number > 0 else None


def _integer(value: object) -> int | None:
    """Return a positive integer or ``None``."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value > 0 else None


__all__ = ["ArtifactCheck", "image_reason", "media_reason", "source_reason"]
