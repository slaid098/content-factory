"""Environment isolation for generator tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

_ENV_NAMES = (
    "VAST_API_TOKEN",
    "GENERATOR_AI_KEY",
    "GENERATOR_AI_URL",
    "GENERATOR_AI_MODEL",
    "GENERATOR_MAX_COST_PER_HOUR",
    "GENERATOR_OUTPUT_DIR",
    "GENERATOR_SERVER_READY_TIMEOUT",
    "GENERATOR_COMFY_HEALTH_TIMEOUT",
    "GENERATOR_STALE_INSTANCE_HOURS",
)


@pytest.fixture(autouse=True)
def _clean_generator_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Clear generator variables so the host environment cannot leak in."""
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    yield
