"""Registry of the layer producers available to a production recipe."""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping

from content_factory.exceptions.generator import ProfileError
from content_factory.schemas.generator.pipeline import LayerProducer

VAST_PRODUCER = "vast"
_CARD_PRODUCER = "card"

_ProducerFactory = Callable[[], LayerProducer]


def _register[Port](
    factories: MutableMapping[str, Callable[[], Port]],
    name: str,
    factory: Callable[[], Port],
    empty_error: str,
) -> None:
    """Store *factory* under a clean *name* or raise *empty_error*."""
    clean = name.strip()
    if not clean:
        raise ProfileError(empty_error)
    factories[clean] = factory


def _resolve[Port](
    factories: Mapping[str, Callable[[], Port]],
    name: str,
    unknown_error: str,
) -> Port:
    """Build the factory stored under *name* or raise *unknown_error*."""
    try:
        factory = factories[name]
    except KeyError as ex:
        allowed = ", ".join(sorted(factories)) or "нет"
        raise ProfileError(f"{unknown_error} {name!r}: допустимы {allowed}") from ex
    return factory()


def _vast() -> LayerProducer:
    """Build the layer adapter of the existing Vast/ComfyUI pipeline."""
    from content_factory.services.production.vast import VastProducer

    return VastProducer()


def _card() -> LayerProducer:
    """Build the layer producer of photo posts from the profile text card."""
    from content_factory.services.production.card import CardProducer

    return CardProducer()


_BUILTINS: Mapping[str, _ProducerFactory] = {
    VAST_PRODUCER: _vast,
    _CARD_PRODUCER: _card,
}


class _FactoryRegistryMixin[Port]:
    """Named factories shared by the layered producer and check registries."""

    _EMPTY_ERROR = "пустое имя"
    _UNKNOWN_ERROR = "неизвестное имя"

    def __init__(self, factories: Mapping[str, Callable[[], Port]] | None = None) -> None:
        """Start with *factories*, or an empty registry when none are given."""
        self._factories: dict[str, Callable[[], Port]] = dict(factories or {})

    def register(self, name: str, factory: Callable[[], Port]) -> None:
        """Register or replace the factory of *name*.

        Args:
            name: Name used by a layer.
            factory: Zero-argument factory of the port instance.

        Raises:
            ProfileError: The name is empty.
        """
        _register(self._factories, name, factory, self._EMPTY_ERROR)

    def resolve(self, name: str) -> Port:
        """Build the port registered under *name*.

        Raises:
            ProfileError: No registration has that name.
        """
        return _resolve(self._factories, name, self._UNKNOWN_ERROR)

    def available(self) -> tuple[str, ...]:
        """Return the registered names in a stable order."""
        return tuple(sorted(self._factories))

    def reset(self) -> None:
        """Drop every registration, including test injections."""
        self._factories.clear()


class ProducerRegistry(_FactoryRegistryMixin[LayerProducer]):
    """Named factories of layered producers with a resettable store for tests."""

    _EMPTY_ERROR = "пустое имя producer"
    _UNKNOWN_ERROR = "неизвестный producer"


_DEFAULT = ProducerRegistry(_BUILTINS)


def default_producers() -> ProducerRegistry:
    """Return the process registry with the built-in producers."""
    return _DEFAULT


def register(name: str, factory: _ProducerFactory) -> None:
    """Register or replace the factory of *name* in the process registry."""
    _DEFAULT.register(name, factory)


def resolve(name: str) -> LayerProducer:
    """Build the producer registered under *name* in the process registry."""
    return _DEFAULT.resolve(name)


def available() -> tuple[str, ...]:
    """Return the process producer names in a stable order."""
    return _DEFAULT.available()


def reset() -> None:
    """Restore the process registry to the built-in producers."""
    _DEFAULT.reset()
    for name, factory in _BUILTINS.items():
        _DEFAULT.register(name, factory)


__all__ = ["ProducerRegistry", "available", "default_producers", "register", "reset", "resolve"]
