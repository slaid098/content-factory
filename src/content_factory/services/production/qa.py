"""Registry of the named QA checks a production step declares."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from content_factory.exceptions.generator import GeneratorError, ProfileError

QA_SOURCE = "source"
QA_FINAL = "final"

QaHandler = Callable[[], str | None]


@dataclass(frozen=True, slots=True)
class _QaCheck:
    """One named check and the pipeline stage it belongs to."""

    name: str
    stage: str


_BUILTINS: Mapping[str, str] = {
    "clef": QA_SOURCE,
    "image": QA_FINAL,
    "media": QA_FINAL,
}


class Registry:
    """Named QA checks with a resettable store for tests."""

    def __init__(self, checks: Mapping[str, str] | None = None) -> None:
        """Start with *checks* (name → stage), or an empty registry."""
        self._checks: dict[str, _QaCheck] = {
            name: _QaCheck(name, stage) for name, stage in (checks or {}).items()
        }

    def register(self, name: str, stage: str) -> None:
        """Register or replace one named check.

        Args:
            name: Check name used by a profile recipe.
            stage: ``source`` before the overlay or ``final`` after it.

        Raises:
            ProfileError: The name is empty.
            GeneratorError: The stage is unknown.
        """
        clean = name.strip()
        if not clean:
            raise ProfileError("пустое имя QA-проверки")
        if stage not in (QA_SOURCE, QA_FINAL):
            raise GeneratorError(f"неизвестный этап QA-проверки {stage!r}")
        self._checks[clean] = _QaCheck(clean, stage)

    def resolve(self, name: str) -> _QaCheck:
        """Return the check registered under *name*.

        Raises:
            ProfileError: No check has that name.
        """
        check = self._checks.get(name)
        if check is None:
            allowed = ", ".join(self.available()) or "нет"
            raise ProfileError(f"неизвестная QA-проверка {name!r}: допустимы {allowed}")
        return check

    def available(self) -> tuple[str, ...]:
        """Return the registered names in a stable order."""
        return tuple(sorted(self._checks))

    def reset(self) -> None:
        """Restore the built-in checks, dropping test injections."""
        self._checks = {name: _QaCheck(name, stage) for name, stage in _BUILTINS.items()}

    def has(self, names: tuple[str, ...], stage: str) -> bool:
        """Return whether *names* declares a check of *stage*."""
        return any(self.resolve(name).stage == stage for name in names)

    def run(
        self,
        names: tuple[str, ...],
        stage: str,
        handlers: Mapping[str, QaHandler],
    ) -> str | None:
        """Run the declared checks of *stage* in list order.

        Args:
            names: QA names exactly as declared by the profile step.
            stage: Pipeline stage whose checks are executed now.
            handlers: Callables of the named checks.

        Returns:
            The first defect reason, or ``None`` when every check passed.

        Raises:
            ProfileError: A name is not registered.
            GeneratorError: A declared check has no handler.
        """
        for name in names:
            check = self.resolve(name)
            if check.stage != stage:
                continue
            handler = handlers.get(name)
            if handler is None:
                raise GeneratorError(f"нет обработчика QA-проверки {name}")
            reason = handler()
            if reason:
                return reason
        return None


_DEFAULT = Registry(_BUILTINS)


def register(name: str, stage: str) -> None:
    """Register or replace one named check in the process registry."""
    _DEFAULT.register(name, stage)


def resolve(name: str) -> _QaCheck:
    """Return the check registered under *name* in the process registry."""
    return _DEFAULT.resolve(name)


def available() -> tuple[str, ...]:
    """Return the process check names in a stable order."""
    return _DEFAULT.available()


def reset() -> None:
    """Restore the process registry to the built-in checks."""
    _DEFAULT.reset()


def has(names: tuple[str, ...], stage: str) -> bool:
    """Return whether *names* declares a process check of *stage*."""
    return _DEFAULT.has(names, stage)


def run(names: tuple[str, ...], stage: str, handlers: Mapping[str, QaHandler]) -> str | None:
    """Run the declared process checks of *stage* in list order."""
    return _DEFAULT.run(names, stage, handlers)


__all__ = [
    "QA_FINAL",
    "QA_SOURCE",
    "QaHandler",
    "Registry",
    "available",
    "has",
    "register",
    "reset",
    "resolve",
    "run",
]
