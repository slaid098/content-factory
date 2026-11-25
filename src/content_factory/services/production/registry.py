"""Registry of the producers available to a production recipe."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from content_factory.exceptions.generator import ProfileError
from content_factory.services.production.protocol import Producer

VAST_PRODUCER = "vast"
CARD_PRODUCER = "card"

_ProducerFactory = Callable[[], Producer]


def _vast() -> Producer:
    """Build the adapter of the existing Vast/ComfyUI pipeline."""
    from content_factory.services.production.vast import VastProducer

    return VastProducer()


def _card() -> Producer:
    """Build the producer of photo posts from the profile text card."""
    from content_factory.services.production.card import CardProducer

    return CardProducer()


_BUILTINS: Mapping[str, _ProducerFactory] = {
    VAST_PRODUCER: _vast,
    CARD_PRODUCER: _card,
}


class Registry:
    """Named producer factories with a resettable store for tests."""

    def __init__(self, factories: Mapping[str, _ProducerFactory] | None = None) -> None:
        """Start with *factories*, or an empty registry when none are given."""
        self._factories: dict[str, _ProducerFactory] = dict(factories or {})

    def register(self, name: str, factory: _ProducerFactory) -> None:
        """Register or replace the factory of *name*.

        Args:
            name: Producer name used by a profile recipe.
            factory: Zero-argument factory of the producer instance.

        Raises:
            ProfileError: The name is empty.
        """
        clean = name.strip()
        if not clean:
            raise ProfileError("пустое имя producer")
        self._factories[clean] = factory

    def resolve(self, name: str) -> Producer:
        """Build the producer registered under *name*.

        Raises:
            ProfileError: No producer has that name.
        """
        try:
            factory = self._factories[name]
        except KeyError as ex:
            allowed = ", ".join(self.available()) or "нет"
            raise ProfileError(f"неизвестный producer {name!r}: допустимы {allowed}") from ex
        return factory()

    def available(self) -> tuple[str, ...]:
        """Return the registered names in a stable order."""
        return tuple(sorted(self._factories))

    def reset(self) -> None:
        """Restore the built-in producers, dropping test injections."""
        self._factories = dict(_BUILTINS)


_DEFAULT = Registry(_BUILTINS)


def register(name: str, factory: _ProducerFactory) -> None:
    """Register or replace the factory of *name* in the process registry."""
    _DEFAULT.register(name, factory)


def resolve(name: str) -> Producer:
    """Build the producer registered under *name* in the process registry."""
    return _DEFAULT.resolve(name)


def available() -> tuple[str, ...]:
    """Return the process producer names in a stable order."""
    return _DEFAULT.available()


def reset() -> None:
    """Restore the process registry to the built-in producers."""
    _DEFAULT.reset()


__all__ = ["Registry", "available", "register", "reset", "resolve"]
