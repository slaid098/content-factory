"""Production capsule door: the layered pipeline, its checks and reports."""

from content_factory.services.production.groups import group_profiles
from content_factory.services.production.pipeline import run_pipeline
from content_factory.services.production.qa import QA_FINAL, QA_SOURCE, default_checks
from content_factory.services.production.qa import run as run_qa
from content_factory.services.production.recipe import DEFAULT_RECIPE, read_recipe
from content_factory.services.production.registry import VAST_PRODUCER, default_producers
from content_factory.services.production.reports import build_report

__all__ = [
    "DEFAULT_RECIPE",
    "QA_FINAL",
    "QA_SOURCE",
    "VAST_PRODUCER",
    "build_report",
    "default_checks",
    "default_producers",
    "group_profiles",
    "read_recipe",
    "run_pipeline",
    "run_qa",
]
