"""Producer adapter of the existing Vast.ai/ComfyUI batch pipeline."""

from __future__ import annotations

from pathlib import Path

from content_factory.config.settings import GeneratorSettings
from content_factory.schemas.content.delivery import ContentType
from content_factory.services.generator import generate_batch
from content_factory.services.production.protocol import ProducedBatch, ProfileRef


class VastProducer:
    """Run the existing generator pipeline as one production step."""

    supported_types = frozenset({ContentType.VIDEO})

    def produce(
        self,
        profile: ProfileRef,
        count: int,
        data_dir: Path,
        settings: GeneratorSettings,
    ) -> ProducedBatch:
        """Produce one profile with the Vast.ai/ComfyUI pipeline."""
        result = generate_batch(
            profiles=profile.name,
            count=count,
            data_dir=data_dir,
            settings=settings,
        )
        return ProducedBatch(
            run_id=result.run_id,
            generated=result.generated,
            failed=result.failed,
            run_dirs=result.run_dirs,
        )


__all__ = ["VastProducer"]
