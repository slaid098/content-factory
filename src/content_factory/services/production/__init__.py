"""Production capsule door: the layered pipeline, its checks and reports."""

from content_factory.services.production.groups import group_profiles
from content_factory.services.production.pipeline import run_pipeline
from content_factory.services.production.qa import QA_FINAL, QA_SOURCE, default_checks
from content_factory.services.production.qa import run as run_qa
from content_factory.services.production.recipe import DEFAULT_RECIPE, filter_recipe, read_recipe
from content_factory.services.production.registry import (
    VAST_PRODUCER,
    card_producer,
    default_producers,
)
from content_factory.services.production.reports import build_report

__all__ = [
    "DEFAULT_RECIPE",
    "QA_FINAL",
    "QA_SOURCE",
    "VAST_PRODUCER",
    "build_report",
    "card_producer",
    "default_checks",
    "default_producers",
    "filter_recipe",
    "group_profiles",
    "read_recipe",
    "run_pipeline",
    "run_qa",
]
