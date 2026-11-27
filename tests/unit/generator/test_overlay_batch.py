"""Overlay batch orchestration: dry-run, resume and QA rejection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.unit.generator.helpers import make_overlay_profile

from content_factory.config.settings import GeneratorSettings
from content_factory.exceptions.generator import GeneratorError
from content_factory.schemas.generator.overlay import OverlayPackage
from content_factory.schemas.generator.server import ServerConnection, ServerInfo
from content_factory.services.generator import comfy, overlay_batch
from content_factory.services.generator.overlay_batch import generate_overlays
from content_factory.services.generator.overlay_plan import plan_overlay
from content_factory.services.generator.overlay_profiles import load_overlay_profile


def _settings(tmp_path: Path) -> GeneratorSettings:
    return GeneratorSettings(
        output_dir=tmp_path / "generated",
        vast_api_token="token",
    )


def _fake_package(tmp_path: Path) -> OverlayPackage:
    return OverlayPackage(
        directory=tmp_path,
        archive=tmp_path / "batch.zip",
        preview=tmp_path / "preview.mp4",
        clips=(),
    )


def test_dry_run_builds_the_plan_without_network_or_directories(tmp_path: Path) -> None:
    make_overlay_profile(tmp_path / "profiles", "overlay/profile_1")

    def no_rent(profile: Any, settings: Any) -> Any:
        raise AssertionError("dry-run must not rent a server")

    run = generate_overlays(
        "overlay/profile_1",
        data_dir=tmp_path,
        dry_run=True,
        session_factory=no_rent,
    )

    assert run.dry_run
    assert len(run.plan.items) == 10
    assert run.accepted == ()
    assert run.package is None
    assert not (tmp_path / "profiles" / "overlay" / "profile_1" / "videos").exists()


def test_existing_clips_that_pass_qa_are_not_generated_again(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = make_overlay_profile(tmp_path / "profiles", "overlay/profile_1")
    videos = directory / "videos"
    videos.mkdir()
    for index in range(1, 11):
        (videos / f"overlay_profile_1_{index:03d}.mp4").write_bytes(b"clip")
    monkeypatch.setattr(overlay_batch, "rejection_reason", lambda *args, **kwargs: None)
    monkeypatch.setattr(overlay_batch, "bright_share", lambda *args, **kwargs: 5.0)
    monkeypatch.setattr(
        overlay_batch,
        "package_overlays",
        lambda profile, accepted, data_dir: _fake_package(tmp_path),
    )
    monkeypatch.setattr(
        overlay_batch.comfy,
        "run_server",
        lambda *args, **kwargs: pytest.fail("all clips are already accepted"),
    )

    run = generate_overlays("overlay/profile_1", data_dir=tmp_path)

    assert len(run.accepted) == 10
    assert len(run.skipped) == 10
    assert run.package is not None


def test_missing_clips_are_generated_and_packed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_overlay_profile(tmp_path / "profiles", "overlay/profile_1")
    monkeypatch.setattr(overlay_batch.media, "ensure_free_space", lambda *args, **kwargs: None)
    monkeypatch.setattr(overlay_batch, "rejection_reason", lambda *args, **kwargs: None)
    monkeypatch.setattr(overlay_batch, "bright_share", lambda *args, **kwargs: 5.0)
    monkeypatch.setattr(
        overlay_batch,
        "package_overlays",
        lambda profile, accepted, data_dir: _fake_package(tmp_path),
    )

    def fake_run(*args: Any, **kwargs: Any) -> tuple[None, tuple[comfy._JobResult, ...]]:
        items = args[2]
        directory = args[4]
        results = []
        for item in items:
            path = directory / item.filename
            path.write_bytes(b"clip")
            results.append(comfy._JobResult(item=item, path=path))
        return None, tuple(results)

    monkeypatch.setattr(overlay_batch.comfy, "run_server", fake_run)

    run = generate_overlays(
        "overlay/profile_1",
        data_dir=tmp_path,
        settings=_settings(tmp_path),
        session_factory=lambda profile, settings: object(),
    )

    assert len(run.accepted) == 10
    assert run.skipped == ()
    assert run.package is not None


def test_batch_without_accepted_clips_raises_and_does_not_pack(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_overlay_profile(tmp_path / "profiles", "overlay/profile_1")
    monkeypatch.setattr(overlay_batch.media, "ensure_free_space", lambda *args, **kwargs: None)
    monkeypatch.setattr(overlay_batch, "rejection_reason", lambda *args, **kwargs: "слабо")
    monkeypatch.setattr(overlay_batch, "bright_share", lambda *args, **kwargs: 0.5)
    monkeypatch.setattr(
        overlay_batch,
        "package_overlays",
        lambda *args, **kwargs: pytest.fail("nothing accepted, packing must not run"),
    )

    def fake_run(*args: Any, **kwargs: Any) -> tuple[None, tuple[comfy._JobResult, ...]]:
        items = args[2]
        directory = args[4]
        for item in items:
            (directory / item.filename).write_bytes(b"clip")
        return None, ()

    monkeypatch.setattr(overlay_batch.comfy, "run_server", fake_run)

    with pytest.raises(GeneratorError, match="не прошёл плотность"):
        generate_overlays(
            "overlay/profile_1",
            data_dir=tmp_path,
            settings=_settings(tmp_path),
            session_factory=lambda profile, settings: object(),
        )


def test_submit_and_download_hooks_match_the_real_server_queue(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    make_overlay_profile(root, "overlay/profile_1")
    profile = load_overlay_profile("overlay/profile_1", root)
    planned = overlay_batch._planned(profile, plan_overlay(profile, "run").items[0])
    connection = ServerConnection(
        contract_id="contract-1",
        ip="127.0.0.1",
        port=8188,
        gpu="L40S",
        image="image",
    )

    class _Transport:
        def submit(self, workflow: dict[str, Any]) -> str:
            del workflow
            return "p1"

        def history(self, prompt_id: str) -> dict[str, Any]:
            return {"outputs": {"15": {"gifs": [{"filename": f"{prompt_id}.mp4"}]}}}

        def system_stats(self) -> dict[str, Any]:
            return {"system": {}}

        def download(self, output: Any) -> bytes:
            del output
            return b"clip"

        def close(self) -> None:
            pass

    class _Session:
        destroyed = 0

        def open(self) -> ServerInfo:
            return ServerInfo(connection=connection)

        def destroy(self) -> None:
            _Session.destroyed += 1

    _, results = comfy.run_server(
        _Session(),
        lambda conn: _Transport(),
        (planned,),
        lambda item, attempt: {"attempt": attempt},
        tmp_path,
        label="overlay/profile_1",
        settings=_settings(tmp_path),
        store=lambda path, content: path.write_bytes(content),
        on_submit=overlay_batch._log_submit,
        on_download=overlay_batch._log_download,
    )

    assert results[0].path == tmp_path / planned.filename
    assert _Session.destroyed == 1


def test_overlay_batch_caps_the_readiness_wait(tmp_path: Path) -> None:
    long_wait = _settings(tmp_path).model_copy(update={"server_ready_timeout": 3600})
    short_wait = long_wait.model_copy(update={"server_ready_timeout": 300})

    assert (
        overlay_batch._bounded_settings(long_wait).server_ready_timeout
        == overlay_batch._READY_TIMEOUT
    )
    assert overlay_batch._bounded_settings(short_wait).server_ready_timeout == 300
