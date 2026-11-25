"""Public path of the uniqueizer engine port.

The protocol is declared with the other shared types so an engine
implementation imports one public module instead of a capsule internal.
"""

from content_factory.schemas.generator.uniqueizer import UniqueizerEngine

__all__ = ["UniqueizerEngine"]
