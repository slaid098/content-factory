"""Service orchestration: dry run, output paths, teardown and failures."""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.unit.generator.helpers import FakeSession, fixture_profile, make_profile

from content_factory.config.settings import GeneratorSettings
from content_factory.exceptions.generator import GeneratorError, ProfileError, RentError
from content_factory.services.generator import media, overlay, qa, service
from content_factory.services.generator.comfy import _OutputFile

_NOW = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
_RUN_ID = "20260101T000000Z"


class _Transport:
    """ComfyUI transport that finishes every prompt immediately."""

    def __init__(self, events: list[str] | None = None, *, fail_first: bool = False) -> None:
        self.events = events if events is not None else []
        self.submitted = 0
        self.closed = False
        self.fail_first = fail_first
        self.prefixes: dict[str, str] = {}

    def submit(self, workflow: dict[str, Any]) -> str:
        self.submitted += 1
        prompt_id = f"prompt-{self.submitted}"
        self.prefixes[prompt_id] = str(workflow["15"]["inputs"]["filename_prefix"])
        self.events.append("submit")
        return prompt_id

    def history(self, prompt_id: str) -> dict[str, Any]:
        return {
            "outputs": {
                "15": {
                    "gifs": [
                        {
                            "filename": f"{self.prefixes[prompt_id]}.mp4",
                            "subfolder": "",
                            "type": "output",
                        }
                    ]
                }
            }
        }

    def download(self, output: _OutputFile) -> bytes:
        if self.fail_first and "_001" in output.filename:
            return b""
        return b"video"

    def close(self) -> None:
        self.closed = True


class _Writer:
    def __init__(self, events: list[str] | None = None) -> None:
        self.events = events if events is not None else []
        self.calls = 0

    def write(self, system_prompt: str) -> str:
        del system_prompt
        self.calls += 1
        self.events.append("text")
        return "<result><block>Текст карточки</block><block>напиши мне</block></result>"


def _settings(tmp_path: Path) -> GeneratorSettings:
    return GeneratorSettings(output_dir=tmp_path / "generated")


def _fake_postprocess(monkeypatch, events: list[str]) -> None:
    def mix(profile, card, source, output, audio) -> None:
        del profile, card, source, audio
        events.append("mix")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final")

    def check(path: Path, expected: float) -> media._MediaInfo:
        return media._MediaInfo(
            path=path,
            duration=expected,
            width=512,
            height=896,
            has_video=True,
            has_audio=True,
            size=path.stat().st_size,
        )

    monkeypatch.setattr(overlay, "mix", mix)
    monkeypatch.setattr(qa, "check_video", check)


def _cursor_path(tmp_path: Path) -> Path:
    return tmp_path / "generated" / "ru" / "dating_profile_2" / "rotation.json"


def _run_batch(
    tmp_path: Path,
    monkeypatch,
    *,
    count: int = 1,
    fail_first: bool = False,
) -> service._BatchResult:
    events: list[str] = []
    session = FakeSession(events)
    _fake_postprocess(monkeypatch, events)
    return service.generate_batch(
        profiles="dating/profile_2",
        count=count,
        data_dir=tmp_path,
        settings=_settings(tmp_path),
        session_factory=lambda profile, settings: session,
        client_factory=lambda connection: _Transport(events, fail_first=fail_first),
        text_writer=_Writer(events),
        store=lambda path, content: path.write_bytes(content),
        rng=random.Random(1),
        now=_NOW,
    )


def test_dry_run_writes_report_without_network(tmp_path: Path) -> None:
    fixture_profile(tmp_path / "profiles")

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry_run must not touch the network")

    result = service.generate_batch(
        profiles="dating/profile_2",
        count=2,
        dry_run=True,
        data_dir=tmp_path,
        settings=_settings(tmp_path),
        session_factory=fail,
        client_factory=fail,
        text_writer=fail,
        store=fail,
        rng=random.Random(1),
        now=_NOW,
    )

    assert result.run_id == _RUN_ID
    assert result.status == "dry_run"
    assert result.generated == 0
    assert result.failed == 0
    report_path = tmp_path / "generated" / "ru" / "dating_profile_2" / _RUN_ID / "run.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "dry_run"
    assert report["server"] is None
    assert len(report["items"]) == 2
    assert not (report_path.parent / "source").exists()
    assert not (report_path.parent / "final").exists()


def test_existing_run_directory_gets_suffix(tmp_path: Path) -> None:
    fixture_profile(tmp_path / "profiles")
    settings = _settings(tmp_path)
    first = service.generate_batch(
        data_dir=tmp_path,
        settings=settings,
        dry_run=True,
        rng=random.Random(1),
        now=_NOW,
    )
    second = service.generate_batch(
        data_dir=tmp_path,
        settings=settings,
        dry_run=True,
        rng=random.Random(2),
        now=_NOW,
    )

    assert first.run_id == _RUN_ID
    assert second.run_id == f"{_RUN_ID}-2"
    assert second.run_dirs != first.run_dirs


def test_batch_writes_final_files_and_destroys_server_before_text(
    tmp_path: Path, monkeypatch
) -> None:
    fixture_profile(tmp_path / "profiles")
    events: list[str] = []
    session = FakeSession(events)
    writer = _Writer(events)
    _fake_postprocess(monkeypatch, events)

    result = service.generate_batch(
        profiles="dating/profile_2",
        count=2,
        data_dir=tmp_path,
        settings=_settings(tmp_path),
        session_factory=lambda profile, settings: session,
        client_factory=lambda connection: _Transport(events),
        text_writer=writer,
        store=lambda path, content: path.write_bytes(content),
        rng=random.Random(1),
        now=_NOW,
    )

    assert result.status == "done"
    assert result.generated == 2
    assert result.failed == 0
    assert session.opened == 1
    assert session.destroyed == 1
    assert events.index("destroy") < events.index("text")
    report = json.loads((result.run_dirs[0] / "run.json").read_text(encoding="utf-8"))
    assert report["status"] == "done"
    assert report["server"]["contract_id"] == "42"
    assert report["totals"] == {"generated": 2, "failed": 0, "bytes": 10}
    for item in report["items"]:
        assert item["status"] == "done"
        assert (result.run_dirs[0] / "final" / item["file"]).is_file()


def test_one_failed_item_does_not_stop_the_batch(tmp_path: Path, monkeypatch) -> None:
    fixture_profile(tmp_path / "profiles")
    events: list[str] = []
    session = FakeSession(events)
    writer = _Writer(events)
    _fake_postprocess(monkeypatch, events)

    result = service.generate_batch(
        profiles="dating/profile_2",
        count=2,
        data_dir=tmp_path,
        settings=_settings(tmp_path),
        session_factory=lambda profile, settings: session,
        client_factory=lambda connection: _Transport(events, fail_first=True),
        text_writer=writer,
        store=lambda path, content: path.write_bytes(content),
        rng=random.Random(1),
        now=_NOW,
    )

    assert result.generated == 1
    assert result.failed == 1
    report = json.loads((result.run_dirs[0] / "run.json").read_text(encoding="utf-8"))
    assert [item["status"] for item in report["items"]] == ["failed", "done"]
    assert (result.run_dirs[0] / "source").is_dir()
    assert writer.calls == 1


def test_server_is_destroyed_when_rent_fails(tmp_path: Path) -> None:
    fixture_profile(tmp_path / "profiles")
    session = FakeSession()
    settings = _settings(tmp_path)

    def fail_open() -> None:
        raise RentError("нет офферов")

    session.open = fail_open  # type: ignore[method-assign]

    with pytest.raises(RentError, match="нет офферов"):
        service.generate_batch(
            data_dir=tmp_path,
            settings=settings,
            session_factory=lambda profile, settings: session,
            now=_NOW,
        )

    assert session.destroyed == 1
    report_path = settings.output_dir / "ru" / "dating_profile_2" / _RUN_ID / "run.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["finished_at"] is not None


def test_profile_error_happens_before_rent(tmp_path: Path) -> None:
    root = tmp_path / "profiles"
    make_profile(root)
    (root / "dating" / "profile_2" / "prompt_template.txt").unlink()

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("profile errors must happen before rent")

    with pytest.raises(ProfileError):
        service.generate_batch(
            data_dir=tmp_path,
            settings=_settings(tmp_path),
            session_factory=fail,
            now=_NOW,
        )


def test_non_positive_count_happens_before_rent(tmp_path: Path) -> None:
    fixture_profile(tmp_path / "profiles")

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("count errors must happen before rent")

    with pytest.raises(GeneratorError, match="count"):
        service.generate_batch(
            count=0,
            data_dir=tmp_path,
            settings=_settings(tmp_path),
            session_factory=fail,
            now=_NOW,
        )


def test_dry_run_does_not_create_the_rotation_cursor(tmp_path: Path) -> None:
    fixture_profile(tmp_path / "profiles")

    service.generate_batch(
        profiles="dating/profile_2",
        count=2,
        dry_run=True,
        data_dir=tmp_path,
        settings=_settings(tmp_path),
        rng=random.Random(1),
        now=_NOW,
    )

    assert not _cursor_path(tmp_path).exists()


def test_done_video_moves_the_cursor_to_its_card(tmp_path: Path, monkeypatch) -> None:
    fixture_profile(tmp_path / "profiles")

    _run_batch(tmp_path, monkeypatch)

    cursor = json.loads(_cursor_path(tmp_path).read_text(encoding="utf-8"))
    assert cursor["last_look_id"] == "c01"
    assert cursor["run_id"] == _RUN_ID


def test_failed_video_keeps_the_previous_cursor(tmp_path: Path, monkeypatch) -> None:
    fixture_profile(tmp_path / "profiles")
    path = _cursor_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "last_look_id": "c01",
                "run_id": "old-run",
                "updated_at": "2025-12-31T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )

    result = _run_batch(tmp_path, monkeypatch, fail_first=True)

    assert result.failed == 1
    cursor = json.loads(path.read_text(encoding="utf-8"))
    assert cursor["last_look_id"] == "c01"
    assert cursor["run_id"] == "old-run"


def test_next_batch_starts_after_the_cursor(tmp_path: Path, monkeypatch) -> None:
    fixture_profile(tmp_path / "profiles")
    _run_batch(tmp_path, monkeypatch)

    second = _run_batch(tmp_path, monkeypatch, count=2)

    report = json.loads((second.run_dirs[0] / "run.json").read_text(encoding="utf-8"))
    assert [item["look_id"] for item in report["items"]] == ["c02", "c03"]
