"""Production capsule door: the old producer path and the layered pipeline."""

from content_factory.services.production.pipeline import run_pipeline
from content_factory.services.production.protocol import (
    LayerCheck,
    LayerProducer,
    PipelineContext,
    ProducedBatch,
    Producer,
    ProfileRef,
)
from content_factory.services.production.qa import QA_FINAL, QA_SOURCE, CheckRegistry
from content_factory.services.production.qa import available as available_qa
from content_factory.services.production.qa import run as run_qa
from content_factory.services.production.registry import VAST_PRODUCER, ProducerRegistry
from content_factory.services.production.registry import available as available_producers
from content_factory.services.production.registry import resolve as resolve_producer

__all__ = [
    "QA_FINAL",
    "QA_SOURCE",
    "VAST_PRODUCER",
    "CheckRegistry",
    "LayerCheck",
    "LayerProducer",
    "PipelineContext",
    "ProducedBatch",
    "Producer",
    "ProducerRegistry",
    "ProfileRef",
    "available_producers",
    "available_qa",
    "resolve_producer",
    "run_pipeline",
    "run_qa",
]
