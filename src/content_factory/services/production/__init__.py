"""Production capsule door: producer and QA registries."""

from content_factory.services.production.qa import (
    QA_FINAL,
    QA_SOURCE,
)
from content_factory.services.production.qa import available as available_qa
from content_factory.services.production.qa import has as qa_has
from content_factory.services.production.qa import run as run_qa
from content_factory.services.production.registry import VAST_PRODUCER
from content_factory.services.production.registry import available as available_producers
from content_factory.services.production.registry import resolve as resolve_producer

__all__ = [
    "QA_FINAL",
    "QA_SOURCE",
    "VAST_PRODUCER",
    "available_producers",
    "available_qa",
    "qa_has",
    "resolve_producer",
    "run_qa",
]
