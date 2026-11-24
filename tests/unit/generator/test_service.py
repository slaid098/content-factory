"""Service orchestration: dry run, output paths, teardown and failures."""

from __future__ import annotations

import json
import random
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.unit.generator.helpers import FakeSession, fixture_profile, make_profile

from content_factory.config.settings import GeneratorSettings
from content_factory.exceptions.generator import (
    ComfyError,
    GeneratorError,
    ProfileError,
    RentError,
    SafetyError,
)
from content_factory.services.generator import media, overlay, publish, qa, safety, service
from content_factory.services.generator.comfy import _OutputFile
from content_factory.services.generator.plan import attempt_seed

_NOW = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
_RUN_ID = "20260101T000000Z"


@pytest.fixture(autouse=True)
def _ignore_host_free_space(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests independent of the free space of the test host."""
    monkeypatch.setattr(media, "ensure_free_space", lambda path: None)


class _Transport:
    """ComfyUI transport that finishes every prompt immediately."""

    def __init__(self, events: list[str] | None = None, *, fail_first: bool = False) -> None:
        self.events = events if events is not None else []
        self.submitted = 0
        self.closed = False
        self.fail_first = fail_first
        self.prefixes: dict[str, str] = {}
        self.seeds: list[int] = []

    def submit(self, workflow: dict[str, Any]) -> str:
        self.submitted += 1
        prompt_id = f"prompt-{self.submitted}"
        self.prefixes[prompt_id] = str(workflow["15"]["inputs"]["filename_prefix"])
        self.seeds.append(int(workflow["11"]["inputs"]["seed"]))
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


class _Checker:
    """Clef checker fake: records calls and replays verdicts or an error."""

    def __init__(
        self,
        events: list[str] | None = None,
        *,
        reasons: list[str | None] | None = None,
        error: SafetyError | None = None,
        preflight_error: SafetyError | None = None,
    ) -> None:
        self.events = events if events is not None else []
        self.reasons = list(reasons or [])
        self.error = error
        self.preflight_error = preflight_error
        self.preflights = 0
        self.calls: list[tuple[str, int]] = []

    def preflight(self) -> None:
        self.preflights += 1
        self.events.append("preflight")
        if self.preflight_error is not None:
            raise self.preflight_error

    def verify(self, item, source: Path, attempt: int) -> str | None:
        assert source.is_file()
        self.calls.append((item.filename, attempt))
        self.events.append("verify")
        if self.error is not None:
            raise self.error
        return self.reasons.pop(0) if self.reasons else None


def _settings(tmp_path: Path, **overrides: Any) -> GeneratorSettings:
    values: dict[str, Any] = {"output_dir": tmp_path / "generated", "safety_enabled": False}
    values.update(overrides)
    return GeneratorSettings(**values)


def _fake_postprocess(monkeypatch, events: list[str]) -> None:
    def mix(profile, card, source, output, audio) -> None:
        del profile, card, source, audio
        events.append("mix")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"final")

    def check(path: Path, expected: float, expect_audio: bool = False) -> media._MediaInfo:
        if expect_audio:
            events.append("audio")
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


def test_publish_stock_hardlinks_final_into_profile_niche(tmp_path: Path) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    final = tmp_path / "generated" / "final.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"video")

    destination = publish.publish_stock(profile, final, tmp_path)

    assert destination == tmp_path / "content" / "dating" / "ru" / "final.mp4"
    assert destination.read_bytes() == b"video"
    assert destination.stat().st_ino == final.stat().st_ino
    assert final.is_file()


def test_publish_stock_copies_when_hardlink_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    final = tmp_path / "generated" / "final.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"video")

    def fail_link(*args: object, **kwargs: object) -> None:
        raise OSError("cross-device link")

    monkeypatch.setattr(publish.os, "link", fail_link)

    destination = publish.publish_stock(profile, final, tmp_path)

    assert destination.read_bytes() == b"video"
    assert destination.stat().st_ino != final.stat().st_ino
    assert final.is_file()


def test_publish_stock_uses_profile_category_when_niche_is_empty(tmp_path: Path) -> None:
    profile = fixture_profile(tmp_path / "profiles", extra={"niche": ""})
    final = tmp_path / "generated" / "final.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"video")

    destination = publish.publish_stock(profile, final, tmp_path)

    assert destination == tmp_path / "content" / "dating" / "ru" / "final.mp4"


def test_publish_stock_never_overwrites_an_existing_file(tmp_path: Path) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    final = tmp_path / "generated" / "final.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"new")
    destination = tmp_path / "content" / "dating" / "ru" / "final.mp4"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"old")

    with pytest.raises(GeneratorError, match="не удалось положить в сток"):
        publish.publish_stock(profile, final, tmp_path)

    assert destination.read_bytes() == b"old"
    assert final.read_bytes() == b"new"


def test_publish_stock_reports_copy_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    final = tmp_path / "generated" / "final.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"video")

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("permission denied")

    monkeypatch.setattr(publish.os, "link", fail)
    monkeypatch.setattr(publish.shutil, "copy2", fail)

    with pytest.raises(GeneratorError, match="не удалось положить в сток"):
        publish.publish_stock(profile, final, tmp_path)

    assert final.is_file()
    assert not (tmp_path / "content" / "dating" / "ru" / "final.mp4").exists()


@pytest.mark.parametrize("niche", ["../escape", "Dating", "dating/ru"])
def test_publish_stock_rejects_an_invalid_niche(tmp_path: Path, niche: str) -> None:
    profile = replace(fixture_profile(tmp_path / "profiles"), niche=niche)
    final = tmp_path / "generated" / "final.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"video")

    with pytest.raises(GeneratorError, match="не удалось положить в сток"):
        publish.publish_stock(profile, final, tmp_path)

    assert not (tmp_path / "escape").exists()
    assert not (tmp_path / "content").exists()


def test_publish_stock_rejects_an_absolute_niche(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    profile = replace(fixture_profile(tmp_path / "profiles"), niche=str(outside))
    final = tmp_path / "generated" / "final.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"video")

    with pytest.raises(GeneratorError, match="не удалось положить в сток"):
        publish.publish_stock(profile, final, tmp_path)

    assert not outside.exists()


@pytest.mark.parametrize("language", ["RU", "ru/../x", "r"])
def test_publish_stock_rejects_an_invalid_language(tmp_path: Path, language: str) -> None:
    profile = replace(fixture_profile(tmp_path / "profiles"), language=language)
    final = tmp_path / "generated" / "final.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"video")

    with pytest.raises(GeneratorError, match="не удалось положить в сток"):
        publish.publish_stock(profile, final, tmp_path)

    assert not (tmp_path / "content").exists()


def test_publish_stock_refuses_a_niche_symlinked_outside_content(tmp_path: Path) -> None:
    profile = fixture_profile(tmp_path / "profiles")
    content = tmp_path / "content"
    content.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (content / "dating").symlink_to(outside, target_is_directory=True)
    final = tmp_path / "generated" / "final.mp4"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"video")

    with pytest.raises(GeneratorError, match="не удалось положить в сток"):
        publish.publish_stock(profile, final, tmp_path)

    assert list(outside.rglob("*.mp4")) == []


def test_invalid_profile_niche_fails_before_the_rent(tmp_path: Path) -> None:
    make_profile(tmp_path / "profiles", extra={"niche": "../escape"})
    events: list[str] = []
    session = FakeSession(events)

    with pytest.raises(ProfileError, match="ниша"):
        service.generate_batch(
            profiles="dating/profile_2",
            data_dir=tmp_path,
            settings=_settings(tmp_path),
            session_factory=lambda profile, settings: session,
            client_factory=lambda connection: _Transport(events),
            text_writer=_Writer(events),
            store=lambda path, content: path.write_bytes(content),
            rng=random.Random(1),
            now=_NOW,
        )

    assert session.opened == 0


def test_publication_error_marks_the_video_failed_and_keeps_the_final(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture_profile(tmp_path / "profiles")
    events: list[str] = []
    _fake_postprocess(monkeypatch, events)

    def fail_publish(profile: object, final: Path, data_root: Path) -> Path:
        del profile, data_root
        raise GeneratorError(f"не удалось положить в сток: {final}")

    monkeypatch.setattr(service, "publish_stock", fail_publish)

    result = service.generate_batch(
        profiles="dating/profile_2",
        data_dir=tmp_path,
        settings=_settings(tmp_path),
        session_factory=lambda profile, settings: FakeSession(events),
        client_factory=lambda connection: _Transport(events),
        text_writer=_Writer(events),
        store=lambda path, content: path.write_bytes(content),
        rng=random.Random(1),
        now=_NOW,
    )

    assert result.generated == 0
    assert result.failed == 1
    report = json.loads((result.run_dirs[0] / "run.json").read_text(encoding="utf-8"))
    item = report["items"][0]
    assert item["status"] == "failed"
    assert "не удалось положить в сток" in item["error"]
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


class _DeadTransport:
    """ComfyUI transport that reports the server dead on the first poll."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.submitted = 0
        self.closed = False

    def submit(self, workflow: dict[str, Any]) -> str:
        del workflow
        self.submitted += 1
        self.events.append("submit")
        return f"prompt-{self.submitted}"

    def history(self, prompt_id: str) -> dict[str, Any]:
        del prompt_id
        raise ComfyError("сервер перестал отвечать")

    def system_stats(self) -> dict[str, Any]:
        raise AssertionError("health checks belong to run_jobs")

    def download(self, output: _OutputFile) -> bytes:
        del output
        raise AssertionError("a dead server has no result to download")

    def close(self) -> None:
        self.closed = True


def test_dead_comfy_server_is_destroyed_and_fails_the_remaining_items(tmp_path: Path) -> None:
    fixture_profile(tmp_path / "profiles")
    events: list[str] = []
    session = FakeSession(events)
    transport = _DeadTransport(events)
    settings = _settings(tmp_path)

    with pytest.raises(ComfyError, match="сервер перестал отвечать"):
        service.generate_batch(
            profiles="dating/profile_2",
            count=2,
            data_dir=tmp_path,
            settings=settings,
            session_factory=lambda profile, settings: session,
            client_factory=lambda connection: transport,
            text_writer=_Writer(events),
            store=lambda path, content: path.write_bytes(content),
            rng=random.Random(1),
            now=_NOW,
        )

    assert transport.closed is True
    assert session.destroyed == 1
    report_path = settings.output_dir / "ru" / "dating_profile_2" / _RUN_ID / "run.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["status"] == "failed"
    assert report["finished_at"] is not None
    assert [item["status"] for item in report["items"]] == ["failed", "failed"]
    assert [item["error"] for item in report["items"]] == ["сервер перестал отвечать"] * 2


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


class _SafeClef:
    """Clef transport that accepts every frame."""

    def __init__(self) -> None:
        self.images: list[bytes] = []

    def scores(self, png: bytes) -> dict[str, float]:
        self.images.append(png)
        return {
            "back": 0.0,
            "short_skirt": 0.0,
            "cleavage": 0.0,
            "all_skin": 0.0,
            "nude": 0.0,
        }


def _run_safety_batch(
    tmp_path: Path,
    monkeypatch,
    *,
    checker: _Checker,
    safety_enabled: bool = True,
    safety_frame_attempts: int = 2,
) -> tuple[service._BatchResult, _Transport]:
    events: list[str] = []
    session = FakeSession(events)
    transport = _Transport(events)
    _fake_postprocess(monkeypatch, events)
    checker.events = events
    result = service.generate_batch(
        profiles="dating/profile_2",
        count=1,
        data_dir=tmp_path,
        settings=_settings(
            tmp_path,
            safety_enabled=safety_enabled,
            safety_frame_attempts=safety_frame_attempts,
        ),
        session_factory=lambda profile, settings: session,
        client_factory=lambda connection: transport,
        text_writer=_Writer(events),
        store=lambda path, content: path.write_bytes(content),
        safety_checker=checker,
        rng=random.Random(1),
        now=_NOW,
    )
    return result, transport


def test_preflight_runs_before_rent(tmp_path: Path, monkeypatch) -> None:
    fixture_profile(tmp_path / "profiles")
    checker = _Checker()

    result, _ = _run_safety_batch(tmp_path, monkeypatch, checker=checker)

    assert result.generated == 1
    assert checker.preflights == 1
    assert checker.events.index("preflight") < checker.events.index("open")


def test_preflight_failure_stops_the_batch_before_rent(tmp_path: Path, monkeypatch) -> None:
    fixture_profile(tmp_path / "profiles")
    checker = _Checker(preflight_error=SafetyError("Clef недоступен"))

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("preflight errors must happen before rent")

    with pytest.raises(SafetyError, match="Clef недоступен"):
        service.generate_batch(
            profiles="dating/profile_2",
            data_dir=tmp_path,
            settings=_settings(tmp_path, safety_enabled=True),
            session_factory=fail,
            client_factory=fail,
            safety_checker=checker,
            now=_NOW,
        )


def test_disabled_safety_skips_preflight_and_frames(tmp_path: Path, monkeypatch) -> None:
    fixture_profile(tmp_path / "profiles")
    events: list[str] = []
    session = FakeSession(events)
    checker = _Checker(preflight_error=SafetyError("must not be called"))
    _fake_postprocess(monkeypatch, events)

    result = service.generate_batch(
        profiles="dating/profile_2",
        data_dir=tmp_path,
        settings=_settings(tmp_path),
        session_factory=lambda profile, settings: session,
        client_factory=lambda connection: _Transport(events),
        text_writer=_Writer(events),
        store=lambda path, content: path.write_bytes(content),
        safety_checker=checker,
        rng=random.Random(1),
        now=_NOW,
    )

    assert result.generated == 1
    assert checker.preflights == 0
    assert checker.calls == []


def test_unsafe_source_is_regenerated_with_the_next_seed(tmp_path: Path, monkeypatch) -> None:
    fixture_profile(tmp_path / "profiles")
    checker = _Checker(reasons=["спина", None])

    result, transport = _run_safety_batch(tmp_path, monkeypatch, checker=checker)

    assert result.generated == 1
    assert result.failed == 0
    assert checker.calls == [
        ("dating_profile_2_20260101T000000Z_001.mp4", 1),
        ("dating_profile_2_20260101T000000Z_001.mp4", 2),
    ]
    report = json.loads((result.run_dirs[0] / "run.json").read_text(encoding="utf-8"))
    planned = report["items"][0]["seed"]
    assert transport.seeds[0] == planned
    assert transport.seeds[1] == attempt_seed(planned, 2)
    assert transport.seeds[0] != transport.seeds[1]
    assert checker.events.index("verify") < checker.events.index("mix")


def test_second_unsafe_source_makes_the_slot_failed(tmp_path: Path, monkeypatch) -> None:
    fixture_profile(tmp_path / "profiles")
    checker = _Checker(reasons=["спина", "спина"])

    result, transport = _run_safety_batch(tmp_path, monkeypatch, checker=checker)

    assert result.generated == 0
    assert result.failed == 1
    assert transport.submitted == 2
    report = json.loads((result.run_dirs[0] / "run.json").read_text(encoding="utf-8"))
    assert report["items"][0]["status"] == "failed"
    assert "спина" in report["items"][0]["error"]


def test_clef_error_fails_the_video_without_regeneration(tmp_path: Path, monkeypatch) -> None:
    fixture_profile(tmp_path / "profiles")
    checker = _Checker(error=SafetyError("Clef недоступен"))

    result, transport = _run_safety_batch(tmp_path, monkeypatch, checker=checker)

    assert result.generated == 0
    assert result.failed == 1
    assert transport.submitted == 1
    report = json.loads((result.run_dirs[0] / "run.json").read_text(encoding="utf-8"))
    assert "Clef недоступен" in report["items"][0]["error"]


def test_twelve_frames_include_the_last_second_of_the_source(tmp_path: Path, monkeypatch) -> None:
    fixture_profile(tmp_path / "profiles")
    clef = _SafeClef()
    moments: list[float] = []

    def probe(path: Path, *, timeout: float) -> media._MediaInfo:
        del timeout
        return media._MediaInfo(
            path=path,
            duration=5.0,
            width=512,
            height=896,
            has_video=True,
            has_audio=True,
            size=20_000,
        )

    def frame(path: Path, seconds: float, *, timeout: float) -> bytes:
        del path, timeout
        moments.append(seconds)
        return b"frame"

    monkeypatch.setattr(media, "probe", probe)
    monkeypatch.setattr(media, "frame_png", frame)
    events: list[str] = []
    session = FakeSession(events)
    _fake_postprocess(monkeypatch, events)

    result = service.generate_batch(
        profiles="dating/profile_2",
        data_dir=tmp_path,
        settings=_settings(tmp_path, safety_enabled=True),
        session_factory=lambda profile, settings: session,
        client_factory=lambda connection: _Transport(events),
        text_writer=_Writer(events),
        store=lambda path, content: path.write_bytes(content),
        safety_checker=safety.FrameChecker(
            _settings(tmp_path, safety_enabled=True),
            client=clef,
        ),
        rng=random.Random(1),
        now=_NOW,
    )

    assert result.generated == 1
    assert len(moments) == 12
    assert moments[0] == pytest.approx(0.1)
    assert moments[-1] == pytest.approx(4.95)
    assert moments == sorted(moments)
    assert len(clef.images) == 13


def test_profile_music_requires_an_audio_track(tmp_path: Path, monkeypatch) -> None:
    fixture_profile(tmp_path / "profiles")
    events: list[str] = []
    session = FakeSession(events)
    _fake_postprocess(monkeypatch, events)

    service.generate_batch(
        profiles="dating/profile_2",
        data_dir=tmp_path,
        settings=_settings(tmp_path),
        session_factory=lambda profile, settings: session,
        client_factory=lambda connection: _Transport(events),
        text_writer=_Writer(events),
        store=lambda path, content: path.write_bytes(content),
        rng=random.Random(1),
        now=_NOW,
    )

    assert "audio" in events


def test_silent_profile_does_not_require_audio(tmp_path: Path, monkeypatch) -> None:
    fixture_profile(tmp_path / "profiles", audio=False)
    events: list[str] = []
    session = FakeSession(events)
    _fake_postprocess(monkeypatch, events)

    service.generate_batch(
        profiles="dating/profile_2",
        data_dir=tmp_path,
        settings=_settings(tmp_path),
        session_factory=lambda profile, settings: session,
        client_factory=lambda connection: _Transport(events),
        text_writer=_Writer(events),
        store=lambda path, content: path.write_bytes(content),
        rng=random.Random(1),
        now=_NOW,
    )

    assert "audio" not in events
