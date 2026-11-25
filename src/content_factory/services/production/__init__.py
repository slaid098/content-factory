"""Production capsule door: the layered pipeline, its checks and reports."""

from content_factory.services.production.groups import group_profiles
from content_factory.services.production.pipeline import run_pipeline
from content_factory.services.production.qa import (
    QA_FINAL,
    QA_SOURCE,
    available_checks,
    default_checks,
)
from content_factory.services.production.qa import run as run_qa
from content_factory.services.production.registry import VAST_PRODUCER, default_producers
from content_factory.services.production.registry import available as available_producers
from content_factory.services.production.registry import resolve as resolve_producer
from content_factory.services.production.reports import build_report

__all__ = [
    "QA_FINAL",
    "QA_SOURCE",
    "VAST_PRODUCER",
    "available_checks",
    "available_producers",
    "build_report",
    "default_checks",
    "default_producers",
    "group_profiles",
    "resolve_producer",
    "run_pipeline",
    "run_qa",
]
