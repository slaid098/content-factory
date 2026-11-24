"""Media wrappers: ffmpeg/ffprobe commands, parsing and the free-space guard."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from content_factory.exceptions.generator import GeneratorError
from content_factory.services.generator import media
from content_factory.services.generator.media import (
    _capture,
    _duration,
    _first_stream,
    _free_bytes,
    _media_info,
    ensure_free_space,
    probe,
    run,
    strip_audio,
    video_seconds,
)


def _completed(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["ffmpeg"], returncode, stdout=stdout, stderr=stderr)


def test_run_accepts_success_and_keeps_the_stderr_tail(monkeypatch) -> None:
    monkeypatch.setattr(media, "_capture", lambda command, timeout: _completed())
    run(["ffmpeg", "-version"])

    monkeypatch.setattr(
        media,
        "_capture",
        lambda command, timeout: _completed(1, stderr="x" * 500 + " tail"),
    )

    with pytest.raises(GeneratorError, match="кодом 1: .*tail"):
        run(["ffmpeg"])


def test_run_reports_a_missing_or_hung_command(monkeypatch) -> None:
    monkeypatch.setattr(media, "_capture", lambda command, timeout: None)

    with pytest.raises(GeneratorError, match="не запустился"):
        run(["ffmpeg"], timeout=2)


def test_probe_returns_none_when_the_file_or_ffprobe_is_unusable(
    tmp_path: Path, monkeypatch
) -> None:
    assert probe(tmp_path / "missing.mp4") is None

    path = tmp_path / "video.mp4"
    path.write_bytes(b"x")
    monkeypatch.setattr(media, "_capture", lambda command, timeout: None)
    assert probe(path) is None

    monkeypatch.setattr(media, "_capture", lambda command, timeout: _completed(1))
    assert probe(path) is None

    monkeypatch.setattr(media, "_capture", lambda command, timeout: _completed(stdout="not json"))
    assert probe(path) is None


def test_probe_parses_streams_and_container_duration(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "video.mp4"
    path.write_bytes(b"x" * 20)
    payload = {
        "format": {"duration": "2.5"},
        "streams": [
            {"codec_type": "video", "width": 512, "height": 896},
            {"codec_type": "audio"},
        ],
    }
    monkeypatch.setattr(
        media,
        "_capture",
        lambda command, timeout: _completed(stdout=json.dumps(payload)),
    )

    info = probe(path)

    assert info is not None
    assert info.duration == 2.5
    assert (info.width, info.height, info.has_video, info.has_audio, info.size) == (
        512,
        896,
        True,
        True,
        20,
    )


def test_media_info_rejects_payloads_without_video(tmp_path: Path) -> None:
    path = tmp_path / "video.mp4"
    path.write_bytes(b"x")

    assert _media_info(path, []) is None
    assert _media_info(path, {"streams": "broken"}) is None
    assert _media_info(path, {"streams": [{"codec_type": "audio"}]}) is None
    assert _media_info(path, {"streams": [{"codec_type": "video"}]}) is None


def test_media_info_returns_none_when_the_file_disappears(tmp_path: Path) -> None:
    payload = {
        "format": {"duration": "2"},
        "streams": [{"codec_type": "video", "width": 1, "height": 1}],
    }

    assert _media_info(tmp_path / "gone.mp4", payload) is None


def test_duration_prefers_container_and_falls_back_to_stream() -> None:
    video = {"duration": "3.5"}

    assert _duration({"format": {"duration": "2.25"}}, video) == 2.25
    assert _duration({"format": {"duration": "broken"}}, video) == 3.5
    assert _duration({"format": "broken"}, video) == 3.5
    assert _duration({"format": {"duration": "0"}}, {"duration": None}) is None


def test_first_stream_skips_non_objects() -> None:
    streams: list[Any] = ["text", {"codec_type": "audio"}]

    assert _first_stream(streams, "video") is None
    assert _first_stream(streams, "audio") == {"codec_type": "audio"}


def test_strip_audio_builds_the_ffmpeg_command(tmp_path: Path, monkeypatch) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(media, "run", lambda command: commands.append(command))

    strip_audio(tmp_path / "in.mp4", tmp_path / "out.mp4")

    assert commands[0][:4] == ["ffmpeg", "-y", "-v", "error"]
    assert commands[0][-1] == str(tmp_path / "out.mp4")


def test_free_bytes_walks_to_an_existing_parent(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "missing" / "deep"
    monkeypatch.setattr(media.shutil, "disk_usage", lambda path: SimpleNamespace(free=123))

    assert _free_bytes(target) == 123

    def broken(path: Path) -> Any:
        raise OSError("no disk")

    monkeypatch.setattr(media.shutil, "disk_usage", broken)
    assert _free_bytes(tmp_path) == 0


def test_ensure_free_space_raises_only_below_the_threshold(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(media.shutil, "disk_usage", lambda path: SimpleNamespace(free=100))
    ensure_free_space(tmp_path, required=50)

    with pytest.raises(GeneratorError, match="мало места"):
        ensure_free_space(tmp_path, required=200)


def test_video_seconds_rejects_zero_fps() -> None:
    assert video_seconds(48, 24) == 2.0

    with pytest.raises(GeneratorError, match="fps"):
        video_seconds(48, 0)


def test_capture_maps_start_and_timeout_errors(monkeypatch) -> None:
    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert kwargs["check"] is False
        return _completed(stdout="ok")

    monkeypatch.setattr(media.subprocess, "run", fake_run)
    assert _capture(["ffprobe"], 1.0) is not None

    def timeout(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(command, 1.0)

    monkeypatch.setattr(media.subprocess, "run", timeout)
    assert _capture(["ffprobe"], 1.0) is None

    def broken(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise OSError("no exec")

    monkeypatch.setattr(media.subprocess, "run", broken)
    assert _capture(["ffprobe"], 1.0) is None


def _completed_bytes(
    returncode: int = 0, stdout: bytes = b"", stderr: bytes = b""
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(["ffmpeg"], returncode, stdout=stdout, stderr=stderr)


def test_frame_png_builds_the_ffmpeg_command(tmp_path: Path, monkeypatch) -> None:
    commands: list[list[str]] = []

    def capture(command: list[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
        del timeout
        commands.append(command)
        return _completed_bytes(stdout=b"png")

    monkeypatch.setattr(media, "_capture_bytes", capture)

    assert media.frame_png(tmp_path / "in.mp4", 1.234) == b"png"
    assert commands[0][:7] == [
        "ffmpeg",
        "-ss",
        "1.234",
        "-i",
        str(tmp_path / "in.mp4"),
        "-frames:v",
        "1",
    ]
    assert commands[0][-5:] == ["-f", "image2pipe", "-vcodec", "png", "-"]


def test_frame_png_clamps_negative_time_and_fails_closed(tmp_path: Path, monkeypatch) -> None:
    seen: list[str] = []

    def capture(command: list[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
        del timeout
        seen.append(command[2])
        return _completed_bytes(stdout=b"png")

    monkeypatch.setattr(media, "_capture_bytes", capture)

    media.frame_png(tmp_path / "in.mp4", -1.0)

    assert seen == ["0.000"]

    monkeypatch.setattr(media, "_capture_bytes", lambda command, timeout: None)
    with pytest.raises(GeneratorError, match="не запустился"):
        media.frame_png(tmp_path / "in.mp4", 1.0)

    monkeypatch.setattr(
        media,
        "_capture_bytes",
        lambda command, timeout: _completed_bytes(1, stderr=b"x" * 500 + b" tail"),
    )
    with pytest.raises(GeneratorError, match="кодом 1: .*tail"):
        media.frame_png(tmp_path / "in.mp4", 1.0)

    monkeypatch.setattr(
        media,
        "_capture_bytes",
        lambda command, timeout: _completed_bytes(stdout=b""),
    )
    with pytest.raises(GeneratorError, match="не вытащил"):
        media.frame_png(tmp_path / "in.mp4", 1.0)
