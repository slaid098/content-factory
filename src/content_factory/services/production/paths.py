"""Paths of one pipeline run inside the generated tree."""

from __future__ import annotations

from pathlib import Path

from content_factory.schemas.generator.pipeline import PipelineContext
from content_factory.utils.run_id import profile_run_dir


def context_run_dir(context: PipelineContext, language: str, slug: str) -> Path:
    """Return the run directory of one profile of the group.

    Raises:
        GeneratorError: The run id is invalid or leaves the profile directory.
    """
    return profile_run_dir(context.settings.output_dir, language, slug, context.run_id)


__all__ = ["context_run_dir"]
