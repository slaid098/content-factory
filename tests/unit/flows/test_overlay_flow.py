"""Overlay Prefect flow: lock discipline and summary."""

from __future__ import annotations

from pathlib import Path

import pytest

from content_factory.exceptions.generator import GeneratorBusyError
from content_factory.flows.overlay_batch import flow as module
from content_factory.schemas.generator.overlay import OverlayPlan, OverlayRun


class _Settings:
    data_dir = Path("/tmp/app-data")


class _Lock:
    def __init__(self) -> None:
        self.released = 0

    def release(self) -> None:
        self.released += 1


def _run(tmp_path: Path) -> OverlayRun:
    plan = OverlayPlan(
        profile="overlay/profile_1",
        run_id="run",
        width=384,
        height=672,
        fps=18,
        items=(),
        seconds=4.94,
    )
    return OverlayRun(
        plan=plan,
        accepted=(tmp_path / "clip.mp4",),
        rejected=(),
        skipped=(),
        densities={},
    )


def test_dry_run_does_not_take_the_generator_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "get_settings", lambda: _Settings())
    monkeypatch.setattr(module, "prepare_data_dirs", lambda settings: None)
    monkeypatch.setattr(module, "generate_overlays", lambda *args, **kwargs: _run(tmp_path))
    monkeypatch.setattr(
        module,
        "try_acquire",
        lambda path: pytest.fail("dry-run must not take the lock"),
    )

    result = module.overlay_batch.fn("overlay/profile_1", dry_run=True)

    assert result.startswith("profile=overlay/profile_1 status=dry_run")


def test_real_run_takes_and_releases_the_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock = _Lock()
    monkeypatch.setattr(module, "get_settings", lambda: _Settings())
    monkeypatch.setattr(module, "prepare_data_dirs", lambda settings: None)
    monkeypatch.setattr(module, "generate_overlays", lambda *args, **kwargs: _run(tmp_path))
    monkeypatch.setattr(module, "try_acquire", lambda path: lock)
    monkeypatch.setattr(module, "lock_path", lambda db_dir, name: Path("/tmp/generator.lock"))

    result = module.overlay_batch.fn("overlay/profile_1")

    assert result.startswith("profile=overlay/profile_1 status=done accepted=1")
    assert lock.released == 1


def test_busy_generator_stops_the_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "get_settings", lambda: _Settings())
    monkeypatch.setattr(module, "prepare_data_dirs", lambda settings: None)
    monkeypatch.setattr(module, "try_acquire", lambda path: None)
    monkeypatch.setattr(module, "lock_path", lambda db_dir, name: Path("/tmp/generator.lock"))

    with pytest.raises(GeneratorBusyError):
        module.overlay_batch.fn("overlay/profile_1")
