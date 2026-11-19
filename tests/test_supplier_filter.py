from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from content_factory.exceptions.errors import ConfigError, SupplierError
from content_factory.schemas.supplier.results import FilterDecision, FilterResult
from content_factory.schemas.supplier.settings import SupplierConfig
from content_factory.schemas.supplier.video import STATUS_APPROVED, Video, VideoMetadata
from content_factory.services.supplier.filter import Filter, filter_videos
from content_factory.services.supplier.repository import Repository
from supplier_helpers import make_config, make_metadata, make_video


class FakeSink:
    def __init__(self) -> None:
        self.decisions: list[tuple[Video, VideoMetadata, FilterDecision]] = []
        self.rejections: list[tuple[Video, str]] = []
        self.failures: list[tuple[Video, str]] = []

    def save_decision(
        self,
        video: Video,
        metadata: VideoMetadata,
        decision: FilterDecision,
    ) -> None:
        self.decisions.append((video, metadata, decision))

    def save_rejection(self, video: Video, reason: str) -> None:
        self.rejections.append((video, reason))

    def save_failure(self, video: Video, reason: str) -> None:
        self.failures.append((video, reason))


def _probe_from(
    values: dict[str, VideoMetadata | Exception],
) -> Callable[[str], VideoMetadata]:
    calls: list[str] = []

    def probe(url: str) -> VideoMetadata:
        calls.append(url)
        value = values[url]
        if isinstance(value, Exception):
            raise value
        return value

    probe.calls = calls  # type: ignore[attr-defined]
    return probe


def test_empty_videos_returns_empty_without_probe() -> None:
    sink = FakeSink()

    result = filter_videos(
        make_config(Path()),
        [],
        set(),
        set(),
        sink,
        probe=lambda url: pytest.fail("probe must not be called"),
    )

    assert result.approved == ()
    assert result.rejected == 0
    assert result.failed == 0
    assert sink.decisions == []


def test_known_ids_used_authors_and_duplicates_are_skipped() -> None:
    first = make_video("1", "alice")
    second = make_video("2", "bob")
    third = make_video("1", "carol")
    sink = FakeSink()

    result = filter_videos(
        make_config(Path()),
        [first, second, third],
        known_ids={"1"},
        used_authors={"bob"},
        sink=sink,
        probe=lambda url: pytest.fail("probe must not be called"),
    )

    assert result.approved == ()
    assert result.rejected == 2
    assert result.failed == 0
    assert [(video.video_id, reason) for video, reason in sink.rejections] == [
        ("1", "already_known"),
        ("2", "author_used"),
    ]


def test_one_approved_video_leaves_later_author_videos_untouched() -> None:
    first = make_video("1")
    second = make_video("2")
    third = make_video("3")
    values = {
        first.url: make_metadata(first, views=5000),
        second.url: make_metadata(second),
    }
    probe = _probe_from(values)
    sink = FakeSink()

    result = filter_videos(
        make_config(Path()),
        [first, second, third],
        set(),
        set(),
        sink,
        probe=probe,
    )

    assert result.approved == (second,)
    assert result.rejected == 1
    assert result.failed == 0
    assert probe.calls == [first.url, second.url]  # type: ignore[attr-defined]
    assert [(video.video_id, decision.approved) for video, _, decision in sink.decisions] == [
        ("1", False),
        ("2", True),
    ]


def test_first_approved_video_skips_rest_of_author() -> None:
    first = make_video("1")
    second = make_video("2")
    probe = _probe_from({first.url: make_metadata(first)})
    sink = FakeSink()

    result = filter_videos(
        make_config(Path()),
        [first, second],
        set(),
        set(),
        sink,
        probe=probe,
    )

    assert result.approved == (first,)
    assert result.rejected == 0
    assert probe.calls == [first.url]  # type: ignore[attr-defined]
    assert len(sink.decisions) == 1


def test_all_author_videos_rejected() -> None:
    first = make_video("1")
    second = make_video("2")
    probe = _probe_from(
        {
            first.url: make_metadata(first, is_aigc=True),
            second.url: make_metadata(second, width=1920, height=1080),
        }
    )
    sink = FakeSink()

    result = filter_videos(
        make_config(Path()),
        [first, second],
        set(),
        set(),
        sink,
        probe=probe,
    )

    assert result.approved == ()
    assert result.rejected == 2
    assert result.failed == 0
    assert [decision.reason for _, _, decision in sink.decisions] == ["aigc", "not_vertical"]


def test_probe_retries_then_succeeds() -> None:
    video = make_video("1")
    calls: list[str] = []

    def probe(url: str) -> VideoMetadata:
        calls.append(url)
        if len(calls) == 1:
            raise SupplierError("temporary")
        return make_metadata(video)

    result = filter_videos(
        make_config(Path()),
        [video],
        set(),
        set(),
        FakeSink(),
        probe=probe,
    )

    assert result.approved == (video,)
    assert result.failed == 0
    assert len(calls) == 2


def test_probe_failure_after_two_attempts() -> None:
    video = make_video("1")
    sink = FakeSink()

    def probe(url: str) -> VideoMetadata:
        raise SupplierError("permanent")

    result = filter_videos(
        make_config(Path()),
        [video],
        set(),
        set(),
        sink,
        probe=probe,
    )

    assert result.approved == ()
    assert result.failed == 1
    assert sink.failures == [(video, "metadata_error")]


def test_failed_author_does_not_stop_other_authors() -> None:
    failed = make_video("1", "alice")
    approved = make_video("2", "bob")
    values = {
        failed.url: SupplierError("permanent"),
        approved.url: make_metadata(approved),
    }
    probe = _probe_from(values)

    result = filter_videos(
        make_config(Path()),
        [failed, approved],
        set(),
        set(),
        FakeSink(),
        probe=probe,
    )

    assert result.approved == (approved,)
    assert result.failed == 1
    assert result.rejected == 0


def test_decision_sink_error_does_not_abort_author() -> None:
    video = make_video("1")

    class FailingSink(FakeSink):
        def save_decision(
            self,
            video: Video,
            metadata: VideoMetadata,
            decision: FilterDecision,
        ) -> None:
            del video, metadata, decision
            raise SupplierError("db is down")

    result = filter_videos(
        make_config(Path()),
        [video],
        set(),
        set(),
        FailingSink(),
        probe=lambda url: make_metadata(video),
    )

    assert result.approved == (video,)
    assert result.rejected == 0


def test_failure_sink_error_does_not_abort_author() -> None:
    video = make_video("1")

    class FailingSink(FakeSink):
        def save_failure(self, video: Video, reason: str) -> None:
            del video, reason
            raise SupplierError("db is down")

    def probe(url: str) -> VideoMetadata:
        raise SupplierError("permanent")

    result = filter_videos(
        make_config(Path()),
        [video],
        set(),
        set(),
        FailingSink(),
        probe=probe,
    )

    assert result.failed == 1


def test_rejection_sink_error_does_not_abort_author() -> None:
    video = make_video("1")

    class FailingSink(FakeSink):
        def save_rejection(self, video: Video, reason: str) -> None:
            del video, reason
            raise SupplierError("db is down")

    result = filter_videos(
        make_config(Path()),
        [video],
        known_ids={"1"},
        used_authors=set(),
        sink=FailingSink(),
        probe=lambda url: pytest.fail("probe must not be called"),
    )

    assert result.rejected == 1


def test_duplicate_video_id_is_probed_once() -> None:
    video = make_video("1")
    probe = _probe_from({video.url: make_metadata(video)})

    result = filter_videos(
        make_config(Path()),
        [video, video],
        set(),
        set(),
        FakeSink(),
        probe=probe,
    )

    assert result.approved == (video,)
    assert len(probe.calls) == 1  # type: ignore[attr-defined]


def test_metadata_author_difference_does_not_change_video_author() -> None:
    video = make_video("1", "alice")
    metadata = make_metadata(video, author_id="bob", author_name="Bob")
    sink = FakeSink()

    result = filter_videos(
        make_config(Path()),
        [video],
        set(),
        set(),
        sink,
        probe=lambda url: metadata,
    )

    assert result.approved == (video,)
    assert sink.decisions[0][0].author == "alice"
    assert sink.decisions[0][1].author_id == "bob"


def test_approved_keeps_input_order_across_interleaved_authors() -> None:
    first = make_video("1", "alice")
    second = make_video("2", "bob")
    third = make_video("3", "alice")
    values = {
        first.url: make_metadata(first, views=5000),
        second.url: make_metadata(second),
        third.url: make_metadata(third),
    }
    probe = _probe_from(values)

    result = filter_videos(
        make_config(Path(), probe_workers=1),
        [first, second, third],
        set(),
        set(),
        FakeSink(),
        probe=probe,
    )

    assert result.approved == (second, third)
    assert result.rejected == 1


def test_default_probe_resolves_proxy_once(monkeypatch: pytest.MonkeyPatch) -> None:
    video = make_video("1")
    calls: list[tuple[str, str, int]] = []

    monkeypatch.setattr(
        "content_factory.services.supplier.filter.resolve_proxy",
        lambda cfg: "http://proxy:8080",
    )

    def fake_fetch(url: str, proxy: str = "", timeout: int = 30) -> VideoMetadata:
        calls.append((url, proxy, timeout))
        return make_metadata(video)

    monkeypatch.setattr(
        "content_factory.services.supplier.filter.fetch_video_metadata",
        fake_fetch,
    )

    first = filter_videos(
        make_config(Path()),
        [video],
        set(),
        set(),
        FakeSink(),
    )
    second = filter_videos(
        make_config(Path()),
        [video],
        set(),
        set(),
        FakeSink(),
    )

    assert first.approved == (video,)
    assert second.approved == (video,)
    assert calls == [
        (video.url, "http://proxy:8080", 30),
        (video.url, "http://proxy:8080", 30),
    ]


def test_default_probe_propagates_proxy_config_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(cfg: SupplierConfig) -> str:
        raise ConfigError("no proxy")

    monkeypatch.setattr("content_factory.services.supplier.filter.resolve_proxy", fail)

    with pytest.raises(ConfigError, match="no proxy"):
        filter_videos(
            make_config(Path()),
            [make_video("1")],
            set(),
            set(),
            FakeSink(),
        )


def test_filter_stage_reads_found_videos_and_saves_decisions(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    repo = Repository(cfg.db_path)
    repo.init_schema()
    video = make_video("1")
    assert repo.add_found(video)
    stage = Filter(cfg, repo, probe=lambda url: make_metadata(video))

    result = stage.filter(limit=60)

    assert result.approved == (video,)
    assert repo.list_videos(STATUS_APPROVED) == [video]


def test_filter_stage_without_found_videos_skips_probe(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    repo = Repository(cfg.db_path)
    repo.init_schema()
    stage = Filter(
        cfg,
        repo,
        probe=lambda url: pytest.fail("probe must not be called"),
    )

    assert stage.filter(limit=60) == FilterResult()


def test_filter_stage_zero_limit_skips_probe(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    repo = Repository(cfg.db_path)
    repo.init_schema()
    assert repo.add_found(make_video("1"))
    stage = Filter(cfg, repo, probe=lambda url: pytest.fail("probe must not be called"))

    assert stage.filter(limit=0) == FilterResult()


def test_filter_stage_only_reads_its_language(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    repo = Repository(cfg.db_path)
    repo.init_schema()
    russian = make_video("1", language="ru")
    english = make_video("2", "bob", language="en")
    assert repo.add_found(russian)
    assert repo.add_found(english)
    stage = Filter(cfg, repo, probe=lambda url: make_metadata(russian))

    result = stage.filter(limit=60)

    assert result.approved == (russian,)
    assert repo.list_videos(STATUS_APPROVED, language="en") == []


def test_filter_pause_is_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    video = make_video("1")
    pauses: list[float] = []
    monkeypatch.setattr(
        "content_factory.services.supplier.filter.time.sleep",
        lambda seconds: pauses.append(seconds),
    )

    result = filter_videos(
        make_config(Path(), filter_pause=0.25),
        [video],
        set(),
        set(),
        FakeSink(),
        probe=lambda url: make_metadata(video),
    )

    assert result.approved == (video,)
    assert pauses == [0.25]


def test_metadata_probe_uses_replace_metadata_for_filters() -> None:
    video = make_video("1")
    result = filter_videos(
        make_config(Path()),
        [video],
        set(),
        set(),
        FakeSink(),
        probe=lambda url: replace(make_metadata(video), views=99),
    )
    assert result.approved == (video,)


def test_failed_probe_still_applies_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    pauses: list[float] = []
    monkeypatch.setattr(
        "content_factory.services.supplier.filter.time.sleep",
        lambda seconds: pauses.append(seconds),
    )

    def probe(url: str) -> VideoMetadata:
        raise SupplierError("permanent")

    result = filter_videos(
        make_config(Path(), filter_pause=0.25),
        [make_video("1")],
        set(),
        set(),
        FakeSink(),
        probe=probe,
    )

    assert result.failed == 1
    assert pauses == [0.25]
