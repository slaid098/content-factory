"""Public port of a production step and its result.

The types are declared in ``schemas.generator.run`` so the shared schema layer
stays free of service imports; this module is the capsule's public path to them.
"""

from content_factory.schemas.generator.run import ProducedBatch, Producer, ProfileRef

__all__ = ["ProducedBatch", "Producer", "ProfileRef"]
