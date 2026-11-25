"""Dating batch generator service capsule."""

from content_factory.services.generator.profiles import load_profile, parse_names
from content_factory.services.generator.service import generate_batch

__all__ = ["generate_batch", "load_profile", "parse_names"]
