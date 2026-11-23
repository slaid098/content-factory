"""Cross-process advisory locks for runtime files.

The API and the Prefect worker are separate processes sharing ``app_data``,
so ``threading.Lock`` only serializes threads of one process. Critical
sections take an exclusive ``flock`` on a file in ``app_data/db`` instead: the
kernel releases the lock when the descriptor closes, so a crashed process
never leaves a stale lock behind.

Each concern has its own lock file. Locks of different files may nest (the
build slot is held while the archive lock is taken), but the same file is
never acquired twice by one thread: ``flock`` is not reentrant.

``fcntl`` exists on Linux and macOS only. Other platforms raise
:class:`LockUnavailableError` on the first lock instead of silently losing
cross-process protection: the deployment (Linux containers) is the supported
target.
"""

from __future__ import annotations

import errno
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from content_factory.exceptions.errors import (
    LockError,
    LockTimeoutError,
    LockUnavailableError,
)

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows has no fcntl
    fcntl = None  # type: ignore[assignment]

DELIVERY_LOCK = "delivery"
DELIVERY_BUILD_LOCK = "delivery-build"
DELIVERY_START_LOCK = "delivery-start"
GENERATOR_LOCK = "generator"
SETTINGS_LOCK = "settings"
SUPPLIER_LOCK = "supplier"
QUERIES_LOCK = "queries"

_DEFAULT_TIMEOUT = 300.0
_LOCK_SUFFIX = ".lock"
_LOCK_MODE = 0o600
_POLL_SECONDS = 0.05


@dataclass(slots=True, eq=False)
class FileLock:
    """An acquired advisory lock that may be released from another thread.

    The descriptor is opened before a worker starts and passed to the worker,
    so the lock covers the whole build even though the owning thread changes.
    """

    _descriptor: int
    _path: Path

    @property
    def path(self) -> Path:
        """Return the lock file path."""
        return self._path

    def release(self) -> None:
        """Release the lock and close its descriptor; safe to call twice."""
        descriptor = self._descriptor
        if descriptor < 0:
            return
        self._descriptor = -1
        module = _flock()
        try:
            module.flock(descriptor, module.LOCK_UN)
        finally:
            os.close(descriptor)

    def __enter__(self) -> FileLock:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()


def lock_path(db_dir: Path, name: str) -> Path:
    """Return ``db_dir/<name>.lock``, creating the directory on first use.

    Args:
        db_dir: Directory that holds the database and its lock files.
        name: Name of the critical section.

    Returns:
        Path of the lock file; the file itself is created by :func:`acquire`.

    Raises:
        LockError: The lock directory cannot be created.
    """
    try:
        db_dir.mkdir(parents=True, exist_ok=True)
    except OSError as ex:
        raise LockError(f"не удалось создать каталог блокировок {db_dir}: {ex}") from ex
    return db_dir / f"{name}{_LOCK_SUFFIX}"


def acquire(path: Path, *, timeout: float = _DEFAULT_TIMEOUT) -> FileLock:
    """Acquire the exclusive lock, polling until *timeout* seconds pass.

    Args:
        path: Lock file path, usually from :func:`lock_path`.
        timeout: Maximum seconds to wait for a competing owner.

    Returns:
        The held lock; call :meth:`FileLock.release` or use it as a context
        manager.

    Raises:
        ValueError: The timeout is negative.
        LockTimeoutError: Another process or thread kept the lock.
        LockError: The lock file cannot be opened or locked.
    """
    if timeout < 0:
        raise ValueError("timeout не может быть отрицательным")
    deadline = time.monotonic() + timeout
    while True:
        lock = try_acquire(path)
        if lock is not None:
            return lock
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LockTimeoutError(f"не удалось захватить блокировку {path} за {timeout:.1f} с")
        time.sleep(min(_POLL_SECONDS, remaining))


@contextmanager
def locked(path: Path, *, timeout: float = _DEFAULT_TIMEOUT) -> Iterator[None]:
    """Hold the exclusive lock for the duration of the block."""
    lock = acquire(path, timeout=timeout)
    try:
        yield
    finally:
        lock.release()


def try_acquire(path: Path) -> FileLock | None:
    """Take the lock once, returning ``None`` while another owner holds it.

    Raises:
        LockUnavailableError: The platform has no ``fcntl``.
        LockError: The lock file cannot be opened or locked.
    """
    if fcntl is None:  # pragma: no cover - Windows has no fcntl
        raise LockUnavailableError(
            "межпроцессные блокировки требуют fcntl (Linux/POSIX); на этой платформе они недоступны"
        )
    descriptor = _open(path)
    module = _flock()
    try:
        module.flock(descriptor, module.LOCK_EX | module.LOCK_NB)
    except OSError as ex:
        os.close(descriptor)
        if ex.errno in (errno.EACCES, errno.EAGAIN):
            return None
        raise LockError(f"не удалось заблокировать {path}: {ex}") from ex
    return FileLock(descriptor, path)


def _open(path: Path) -> int:
    """Open the lock file with private permissions."""
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, _LOCK_MODE)
    except OSError as ex:
        raise LockError(f"не удалось открыть lock-файл {path}: {ex}") from ex
    try:
        os.fchmod(descriptor, _LOCK_MODE)
    except OSError as ex:
        os.close(descriptor)
        raise LockError(f"не удалось выставить права lock-файла {path}: {ex}") from ex
    return descriptor


def _flock() -> Any:
    """Return the ``fcntl`` module; callers verify it is available first."""
    return fcntl
