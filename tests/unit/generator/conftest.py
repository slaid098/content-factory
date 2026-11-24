"""Environment isolation for generator tests."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

_ENV_NAMES = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "VAST_API_TOKEN",
    "GENERATOR_MODEL",
    "GENERATOR_MAX_COST_PER_HOUR",
    "GENERATOR_OUTPUT_DIR",
    "GENERATOR_SERVER_READY_TIMEOUT",
    "GENERATOR_RENT_POOL_SIZE",
    "GENERATOR_VIDEOS_PER_SERVER",
    "GENERATOR_MAX_SERVERS",
    "GENERATOR_OFFER_REFRESH_SECONDS",
    "GENERATOR_COMFY_HEALTH_TIMEOUT",
    "GENERATOR_STALE_INSTANCE_HOURS",
    "GENERATOR_SAFETY_ENABLED",
    "GENERATOR_SAFETY_FRAMES",
    "GENERATOR_SAFETY_THRESHOLD",
    "GENERATOR_SAFETY_NUDE_THRESHOLD",
    "GENERATOR_SAFETY_FRAME_ATTEMPTS",
    "GENERATOR_CLEF_MODEL",
)


@pytest.fixture(autouse=True)
def _clean_generator_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Clear generator variables so the host environment cannot leak in."""
    for name in _ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    yield
