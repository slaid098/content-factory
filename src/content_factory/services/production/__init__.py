"""Production capsule door: producer and QA registries."""

from content_factory.services.production.protocol import ProducedBatch, Producer, ProfileRef
from content_factory.services.production.qa import (
    QA_FINAL,
    QA_SOURCE,
)
from content_factory.services.production.qa import available as available_qa
from content_factory.services.production.qa import run as run_qa
from content_factory.services.production.registry import VAST_PRODUCER
from content_factory.services.production.registry import available as available_producers
from content_factory.services.production.registry import resolve as resolve_producer

__all__ = [
    "QA_FINAL",
    "QA_SOURCE",
    "VAST_PRODUCER",
    "ProducedBatch",
    "Producer",
    "ProfileRef",
    "available_producers",
    "available_qa",
    "resolve_producer",
    "run_qa",
]
