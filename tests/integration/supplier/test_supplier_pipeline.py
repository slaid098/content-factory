from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path

import pytest
from tests.unit.supplier.supplier_helpers import make_config, make_metadata, make_video

from content_factory.schemas.supplier.progress import Budget
from content_factory.schemas.supplier.results import (
    DownloadResult,
    FilterDecision,
    FilterResult,
    FindResult,
    LanguageResult,
    StopReason,
)
from content_factory.schemas.supplier.search import QuerySpec
from content_factory.schemas.supplier.video import (
    STATUS_APPROVED,
    STATUS_FOUND,
    DownloadedVideo,
    Video,
)
from content_factory.services.supplier import pipeline
from content_factory.services.supplier.pipeline import run_language
from content_factory.services.supplier.repository import Repository


class _UnusedStage:
    """Stage that fails the test when its work is unexpectedly used."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def find(self, *args: object, **kwargs: object) -> FindResult:
        raise AssertionError("finder must not be used")

    def filter(self, *args: object, **kwargs: object) -> FilterResult:
        raise AssertionError("filter must not be used")

    def download(self, *args: object, **kwargs: object) -> DownloadResult:
        raise AssertionError("downloader must not be used")


@pytest.fixture(autouse=True)
def _unused_stages(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("Finder", "Filter", "Downloader"):
        monkeypatch.setattr(pipeline, name, _UnusedStage)


def _repo(tmp_path: Path) -> Repository:
    repo = Repository(tmp_path / "app_data" / "db" / "db.sqlite3")
    return repo


def _query(name: str = "query") -> QuerySpec:
    return QuerySpec(query=name, language="ru", source=Path("queries/ru.txt"))


def _save_download(repo: Repository, video: Video, sha: str = "sha") -> None:
    assert repo.add_found(video)
    repo.save_download(
        DownloadedVideo(
            video=video,
            path=f"content/ru/{sha}.mp4",
            sha256=sha,
            size=1,
        )
    )


def _approving_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    def factory(cfg: object, repo: Repository) -> object:
        class Stage:
            def filter(self, limit: int) -> FilterResult:
                videos = repo.list_videos(
                    STATUS_FOUND,
                    language=cfg.language,
                    limit=limit,
                )
                for video in videos:
                    repo.save_decision(video, make_metadata(video), FilterDecision(True))
                return FilterResult(approved=tuple(videos))

        return Stage()

    monkeypatch.setattr(pipeline, "Filter", factory)


def _saving_downloader(
    monkeypatch: pytest.MonkeyPatch,
    limits: list[int] | None = None,
) -> None:
    def factory(cfg: object, repo: Repository) -> object:
        class Stage:
            def download(self, limit: int) -> DownloadResult:
                if limits is not None:
                    limits.append(limit)
                videos = repo.list_videos(
                    STATUS_APPROVED,
                    language=cfg.language,
                    limit=limit,
                )
                items: list[DownloadedVideo] = []
                for video in videos:
                    item = DownloadedVideo(
                        video=video,
                        path=f"content/{video.language}/{video.video_id}.mp4",
                        sha256=video.video_id,
                        size=1,
                    )
                    repo.save_download(item)
                    items.append(item)
                return DownloadResult(downloaded=tuple(items))

        return Stage()

    monkeypatch.setattr(pipeline, "Downloader", factory)


def _finder(
    monkeypatch: pytest.MonkeyPatch,
    callback: Callable[[Repository, list[QuerySpec], int], FindResult],
) -> None:
    def factory(cfg: object, repo: Repository, mcp: object) -> object:
        del mcp

        class Stage:
            def find(self, queries: list[QuerySpec], limit: int) -> FindResult:
                return callback(repo, queries, limit)

        return Stage()

    monkeypatch.setattr(pipeline, "Finder", factory)


def test_target_reached_before_any_stage(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _save_download(repo, make_video("1"))

    result = run_language(
        make_config(tmp_path),
        repo=repo,
        mcp=object(),
        queries=[],
        target=1,
        budget=Budget(queries=10, filters=10),
        deadline=time.monotonic() + 60,
    )

    assert result == LanguageResult(downloaded=0, stop_reason=StopReason.TARGET_REACHED)


def test_deadline_is_checked_before_work(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    result = run_language(
        make_config(tmp_path),
        repo=repo,
        mcp=object(),
        queries=[_query()],
        target=1,
        budget=Budget(queries=10, filters=10),
        deadline=time.monotonic() - 1,
    )

    assert result == LanguageResult(downloaded=0, stop_reason=StopReason.DURATION_EXCEEDED)


def test_deadline_is_checked_before_filter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    assert repo.add_found(make_video("1"))
    clock = iter([0.0, 100.0])
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: next(clock))

    result = run_language(
        make_config(tmp_path),
        repo=repo,
        mcp=object(),
        queries=[_query()],
        target=1,
        budget=Budget(queries=10, filters=10),
        deadline=50.0,
    )

    assert result.stop_reason is StopReason.DURATION_EXCEEDED


def test_deadline_is_checked_before_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    video = make_video("1")
    assert repo.add_found(video)
    repo.save_decision(video, make_metadata(video), FilterDecision(True))
    clock = iter([0.0, 100.0])
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: next(clock))

    result = run_language(
        make_config(tmp_path),
        repo=repo,
        mcp=object(),
        queries=[_query()],
        target=1,
        budget=Budget(queries=10, filters=10),
        deadline=50.0,
    )

    assert result.stop_reason is StopReason.DURATION_EXCEEDED


def test_deadline_is_checked_before_finder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    clock = iter([0.0, 100.0])
    monkeypatch.setattr(pipeline.time, "monotonic", lambda: next(clock))

    result = run_language(
        make_config(tmp_path),
        repo=repo,
        mcp=object(),
        queries=[_query()],
        target=1,
        budget=Budget(queries=10, filters=10),
        deadline=50.0,
    )

    assert result.stop_reason is StopReason.DURATION_EXCEEDED


def test_leftovers_are_filtered_and_downloaded_before_searching(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    video = make_video("1")
    assert repo.add_found(video)
    _approving_filter(monkeypatch)
    _saving_downloader(monkeypatch)
    budget = Budget(queries=10, filters=10)

    result = run_language(
        make_config(tmp_path),
        repo=repo,
        mcp=object(),
        queries=[],
        target=1,
        budget=budget,
        deadline=time.monotonic() + 60,
    )

    assert result.downloaded == 1
    assert result.stop_reason is StopReason.TARGET_REACHED
    assert result.found == 0
    assert repo.downloaded_count("ru") == 1
    assert budget.queries == 10
    assert budget.filters == 9


def test_search_then_filter_and_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    video = make_video("1")
    limits: list[int] = []

    def find(repo: Repository, queries: list[QuerySpec], limit: int) -> FindResult:
        assert [item.query for item in queries] == ["query"]
        limits.append(limit)
        assert repo.add_found(video)
        return FindResult(found=1, queries_run=1)

    _finder(monkeypatch, find)
    _approving_filter(monkeypatch)
    _saving_downloader(monkeypatch)
    budget = Budget(queries=10, filters=100)

    result = run_language(
        make_config(tmp_path),
        repo=repo,
        mcp=object(),
        queries=[_query()],
        target=1,
        budget=budget,
        deadline=time.monotonic() + 60,
    )

    assert result.downloaded == 1
    assert result.found == 1
    assert result.approved == 1
    assert result.queries_run == 1
    assert result.stop_reason is StopReason.TARGET_REACHED
    assert limits == [12]
    assert budget.queries == 9
    assert budget.filters == 99


def test_captcha_stops_language_immediately(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    blocked_until = "2026-01-01T00:00:00+00:00"

    def find(repo: Repository, queries: list[QuerySpec], limit: int) -> FindResult:
        del repo, queries, limit
        return FindResult(queries_run=1, captcha=True, blocked_until=blocked_until)

    _finder(monkeypatch, find)
    budget = Budget(queries=10, filters=100)

    result = run_language(
        make_config(tmp_path),
        repo=repo,
        mcp=object(),
        queries=[_query()],
        target=1,
        budget=budget,
        deadline=time.monotonic() + 60,
    )

    assert result.stop_reason is StopReason.CAPTCHA
    assert result.blocked_until == blocked_until
    assert result.downloaded == 0
    assert budget.queries == 9


def test_exhausted_filter_budget_returns_stock_shortfall(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    result = run_language(
        make_config(tmp_path),
        repo=repo,
        mcp=object(),
        queries=[_query()],
        target=1,
        budget=Budget(queries=10, filters=0),
        deadline=time.monotonic() + 60,
    )

    assert result == LanguageResult(downloaded=0, stop_reason=StopReason.STOCK_SHORTFALL)


def test_no_queries_wins_over_exhausted_filter_budget(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    result = run_language(
        make_config(tmp_path),
        repo=repo,
        mcp=object(),
        queries=[],
        target=1,
        budget=Budget(queries=10, filters=0),
        deadline=time.monotonic() + 60,
        no_queries=True,
    )

    assert result == LanguageResult(downloaded=0, stop_reason=StopReason.NO_QUERIES)


def test_no_queries_returns_queries_exhausted(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    result = run_language(
        make_config(tmp_path),
        repo=repo,
        mcp=object(),
        queries=[],
        target=1,
        budget=Budget(queries=10, filters=10),
        deadline=time.monotonic() + 60,
    )

    assert result == LanguageResult(downloaded=0, stop_reason=StopReason.QUERIES_EXHAUSTED)


def test_no_queries_flag_returns_no_queries(tmp_path: Path) -> None:
    repo = _repo(tmp_path)

    result = run_language(
        make_config(tmp_path),
        repo=repo,
        mcp=object(),
        queries=[],
        target=1,
        budget=Budget(queries=10, filters=10),
        deadline=time.monotonic() + 60,
        no_queries=True,
    )

    assert result == LanguageResult(downloaded=0, stop_reason=StopReason.NO_QUERIES)


def test_no_queries_flag_still_drains_leftovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    video = make_video("1")
    assert repo.add_found(video)
    _approving_filter(monkeypatch)
    _saving_downloader(monkeypatch)

    result = run_language(
        make_config(tmp_path),
        repo=repo,
        mcp=object(),
        queries=[],
        target=1,
        budget=Budget(queries=10, filters=10),
        deadline=time.monotonic() + 60,
        no_queries=True,
    )

    assert result.downloaded == 1
    assert result.stop_reason is StopReason.TARGET_REACHED


def test_finder_without_queries_or_results_is_no_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)

    def find(repo: Repository, queries: list[QuerySpec], limit: int) -> FindResult:
        del repo, queries, limit
        return FindResult()

    _finder(monkeypatch, find)

    result = run_language(
        make_config(tmp_path),
        repo=repo,
        mcp=object(),
        queries=[_query()],
        target=1,
        budget=Budget(queries=10, filters=10),
        deadline=time.monotonic() + 60,
    )

    assert result == LanguageResult(downloaded=0, stop_reason=StopReason.NO_PROGRESS)


def test_failed_downloads_are_no_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    video = make_video("1")

    def find(repo: Repository, queries: list[QuerySpec], limit: int) -> FindResult:
        del queries, limit
        assert repo.add_found(video)
        return FindResult(found=1, queries_run=1)

    def factory(cfg: object, repo: Repository) -> object:
        del cfg

        class Stage:
            def download(self, limit: int) -> DownloadResult:
                del limit
                repo.save_failure(video, "network")
                return DownloadResult(failed=1)

        return Stage()

    _finder(monkeypatch, find)
    _approving_filter(monkeypatch)
    monkeypatch.setattr(pipeline, "Downloader", factory)

    result = run_language(
        make_config(tmp_path),
        repo=repo,
        mcp=object(),
        queries=[_query()],
        target=1,
        budget=Budget(queries=10, filters=10),
        deadline=time.monotonic() + 60,
    )

    assert result.stop_reason is StopReason.NO_PROGRESS
    assert result.downloaded == 0
    assert result.failed == 1


def test_query_budget_is_shared_across_search_batches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    video = make_video("1")
    batches: list[list[str]] = []

    def find(repo: Repository, queries: list[QuerySpec], limit: int) -> FindResult:
        del limit
        batches.append([item.query for item in queries])
        assert repo.add_found(video)
        return FindResult(found=1, queries_run=2)

    _finder(monkeypatch, find)
    _approving_filter(monkeypatch)
    _saving_downloader(monkeypatch)
    budget = Budget(queries=2, filters=100)

    result = run_language(
        make_config(tmp_path),
        repo=repo,
        mcp=object(),
        queries=[_query("one"), _query("two"), _query("three")],
        target=1,
        budget=budget,
        deadline=time.monotonic() + 60,
    )

    assert result.stop_reason is StopReason.TARGET_REACHED
    assert batches == [["one", "two"]]
    assert budget.queries == 0


def test_download_limit_is_remaining_need(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    _save_download(repo, make_video("1", "alice"), sha="sha-1")
    for video in (make_video("2", "bob"), make_video("3", "carol")):
        assert repo.add_found(video)
        repo.save_decision(video, make_metadata(video), FilterDecision(True))
    limits: list[int] = []
    _saving_downloader(monkeypatch, limits)

    result = run_language(
        make_config(tmp_path),
        repo=repo,
        mcp=object(),
        queries=[],
        target=3,
        budget=Budget(queries=10, filters=10),
        deadline=time.monotonic() + 60,
    )

    assert result.downloaded == 2
    assert result.stop_reason is StopReason.TARGET_REACHED
    assert limits == [2]
    assert repo.downloaded_count("ru") == 3


def test_download_rejections_are_counted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    video = make_video("1")
    assert repo.add_found(video)
    repo.save_decision(video, make_metadata(video), FilterDecision(True))

    def factory(cfg: object, repo: Repository) -> object:
        del cfg, repo

        class Stage:
            def download(self, limit: int) -> DownloadResult:
                del limit
                return DownloadResult(rejected=1)

        return Stage()

    monkeypatch.setattr(pipeline, "Downloader", factory)

    result = run_language(
        make_config(tmp_path),
        repo=repo,
        mcp=object(),
        queries=[],
        target=1,
        budget=Budget(queries=10, filters=10),
        deadline=time.monotonic() + 60,
    )

    assert result.rejected == 1
    assert result.downloaded == 0


def test_rejected_candidates_are_counted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    videos = [make_video(str(index), f"author-{index}") for index in range(3)]

    def find(repo: Repository, queries: list[QuerySpec], limit: int) -> FindResult:
        del queries, limit
        for video in videos:
            assert repo.add_found(video)
        return FindResult(found=3, queries_run=1)

    def factory(cfg: object, repo: Repository) -> object:
        del cfg

        class Stage:
            def filter(self, limit: int) -> FilterResult:
                pending = repo.list_videos(STATUS_FOUND, language="ru", limit=limit)
                for video in pending:
                    repo.save_rejection(video, "test")
                return FilterResult(rejected=len(pending), failed=0)

        return Stage()

    _finder(monkeypatch, find)
    monkeypatch.setattr(pipeline, "Filter", factory)

    result = run_language(
        make_config(tmp_path),
        repo=repo,
        mcp=object(),
        queries=[_query()],
        target=1,
        budget=Budget(queries=10, filters=10),
        deadline=time.monotonic() + 60,
    )

    assert result.stop_reason is StopReason.QUERIES_EXHAUSTED
    assert result.rejected == 3
    assert result.downloaded == 0
