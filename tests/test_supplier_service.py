from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from content_factory.config.settings import Settings
from content_factory.constants import CONTENT_DIR, DB_DIR, DB_FILENAME, QUERIES_DIR
from content_factory.exceptions.errors import SupplierError
from content_factory.schemas.config.runtime import RunConfig, SearchConfig
from content_factory.schemas.supplier.results import (
    FilterDecision,
    LanguageResult,
    ProduceResult,
    StopReason,
)
from content_factory.schemas.supplier.video import (
    STATUS_DOWNLOADED,
    STATUS_REJECTED,
    DownloadedVideo,
)
from content_factory.services.supplier import downloader, finder, service
from content_factory.services.supplier.repository import Repository
from supplier_helpers import (
    make_metadata,
    make_runtime,
    make_supplier_settings,
    make_video,
)

RU_URL = "https://www.tiktok.com/@alice/video/111"
EN_URL = "https://www.tiktok.com/@bob/video/222"
SECOND_RU_URL = "https://www.tiktok.com/@carol/video/333"

RU_DOM = """
<a class="AVideoContainer" href="https://www.tiktok.com/@alice/video/111..." [r2]
<strong class="video-count" [r3] "5"
"""

EN_DOM = """
<a class="AVideoContainer" href="https://www.tiktok.com/@bob/video/222..." [r4]
<strong class="video-count" [r5] "7"
"""

SECOND_RU_DOM = """
<a class="AVideoContainer" href="https://www.tiktok.com/@carol/video/333..." [r2]
<strong class="video-count" [r3] "5"
"""


class FakeMcp:
    """BitBrowser MCP double that serves one search card per language."""

    instances: list[FakeMcp] = []
    ru_url = RU_URL
    ru_dom = RU_DOM
    en_url = EN_URL
    en_dom = EN_DOM

    def __init__(self, base_url: str, timeout: int = 90) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.calls: list[tuple[object, ...]] = []
        self.current_url = ""
        self.current_dom = self.ru_dom
        FakeMcp.instances.append(self)

    def open_profile(self, profile_id: str, headless: bool = False) -> None:
        self.calls.append(("open_profile", profile_id, headless))

    def close_profile(self, profile_id: str) -> None:
        self.calls.append(("close_profile", profile_id))

    def session(self, profile_id: str) -> FakeMcp:
        self.calls.append(("session", profile_id))
        return self

    def navigate(
        self,
        url: str,
        wait_until: str = "domcontentloaded",
        timeout_ms: int | None = None,
    ) -> None:
        del wait_until, timeout_ms
        self.calls.append(("navigate", url))
        self.current_url = url
        query = parse_qs(urlsplit(url).query).get("q", [""])[0]
        self.current_dom = (
            self.ru_dom if re.search(r"[\u0400-\u04FF]", unquote(query)) else self.en_dom
        )

    def wait_for_selector(
        self,
        selector: str,
        timeout_ms: int | None = None,
    ) -> None:
        del selector, timeout_ms
        self.calls.append(("wait_for_selector",))

    def get_dom(self, **kwargs: object) -> str:
        del kwargs
        return self.current_dom

    def get_attribute(
        self,
        attribute: str,
        ref: str | None = None,
        selector: str | None = None,
    ) -> str | None:
        del attribute, selector
        return {"r2": self.ru_url, "r4": self.en_url}.get(ref or "")

    def scroll(self, direction: str = "down", amount: int = 700) -> None:
        del direction, amount

    def human_idle(self, duration_ms: int) -> None:
        del duration_ms

    def url(self) -> str:
        return self.current_url

    def count(self, name: str) -> int:
        return sum(1 for call in self.calls if call[0] == name)


@pytest.fixture(autouse=True)
def _reset_fakes() -> None:
    FakeMcp.instances.clear()


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path)


def _produce(
    tmp_path: Path,
    plan: dict[str, int],
    *,
    runtime: object | None = None,
    supplier_settings: object | None = None,
) -> ProduceResult:
    return service.produce(
        plan,
        runtime=runtime or make_runtime(),
        settings=_settings(tmp_path),
        supplier_settings=supplier_settings or make_supplier_settings(),
    )


def _patch_run_language(
    monkeypatch: pytest.MonkeyPatch,
    outcomes: dict[str, LanguageResult],
) -> list[tuple[str, int]]:
    calls: list[tuple[str, int]] = []

    def fake_run(
        cfg: object,
        *,
        repo: Repository,
        mcp: object,
        queries: object,
        target: int,
        budget: object,
        deadline: float,
        no_queries: bool = False,
    ) -> LanguageResult:
        del repo, mcp, queries, budget, deadline, no_queries
        language = cfg.language
        calls.append((language, target))
        return outcomes[language]

    monkeypatch.setattr(service, "run_language", fake_run)
    return calls


def test_empty_plan_returns_enough_stock_without_mcp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service,
        "McpClient",
        lambda *args, **kwargs: pytest.fail("MCP must not be used"),
    )

    result = _produce(tmp_path, {})

    assert result == ProduceResult({}, 0, StopReason.ENOUGH_STOCK)
    assert not (tmp_path / CONTENT_DIR).exists()


def test_blank_profile_raises_before_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service,
        "McpClient",
        lambda *args, **kwargs: pytest.fail("MCP must not be used"),
    )

    with pytest.raises(SupplierError, match="SUPPLIER_PROFILE_ID"):
        _produce(
            tmp_path,
            {"ru": 1},
            supplier_settings=make_supplier_settings(profile_id="   "),
        )

    assert not (tmp_path / DB_DIR / DB_FILENAME).exists()


def test_queries_are_seeded_once_and_never_overwritten(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "McpClient", FakeMcp)
    _patch_run_language(
        monkeypatch,
        {"ru": LanguageResult(0, StopReason.NO_PROGRESS)},
    )
    seed = (Path(service.__file__).parent / "queries" / "ru.txt").read_text(encoding="utf-8")

    first = _produce(tmp_path, {"ru": 1})

    queries_path = tmp_path / QUERIES_DIR / "ru.txt"
    assert first.stop_reason is StopReason.NO_PROGRESS
    assert queries_path.read_text(encoding="utf-8") == seed
    assert list(queries_path.parent.glob(".*.tmp")) == []

    queries_path.write_text("# custom list\n", encoding="utf-8")
    _produce(tmp_path, {"ru": 1})

    assert queries_path.read_text(encoding="utf-8") == "# custom list\n"


def test_languages_run_sequentially_and_profile_opens_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "McpClient", FakeMcp)
    calls = _patch_run_language(
        monkeypatch,
        {
            "ru": LanguageResult(2, StopReason.TARGET_REACHED),
            "en": LanguageResult(1, StopReason.TARGET_REACHED),
        },
    )

    result = _produce(tmp_path, {"ru": 2, "en": 1})

    mcp = FakeMcp.instances[0]
    assert calls == [("ru", 2), ("en", 1)]
    assert result.produced_by_language == {"ru": 2, "en": 1}
    assert result.produced_total == 3
    assert result.stop_reason is StopReason.TARGET_REACHED
    assert mcp.count("open_profile") == 1
    assert mcp.count("close_profile") == 1
    assert mcp.calls[0] == ("open_profile", "profile-1", True)
    assert mcp.calls[-1] == ("close_profile", "profile-1")
    assert (tmp_path / DB_DIR / DB_FILENAME).is_file()
    assert (tmp_path / CONTENT_DIR / "ru").is_dir()
    assert (tmp_path / CONTENT_DIR / "en").is_dir()


def test_existing_downloads_are_added_to_the_language_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "McpClient", FakeMcp)
    repo = Repository(tmp_path / DB_DIR / DB_FILENAME)
    repo.init_schema()
    existing = make_video("old", "old-author")
    assert repo.add_found(existing)
    repo.save_download(
        DownloadedVideo(video=existing, path="content/ru/old.mp4", sha256="old", size=1)
    )
    calls = _patch_run_language(
        monkeypatch,
        {"ru": LanguageResult(2, StopReason.TARGET_REACHED)},
    )

    _produce(tmp_path, {"ru": 2})

    assert calls == [("ru", 3)]


def test_captcha_stops_remaining_languages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "McpClient", FakeMcp)
    blocked_until = "2026-01-01T00:00:00+00:00"
    calls = _patch_run_language(
        monkeypatch,
        {
            "ru": LanguageResult(0, StopReason.CAPTCHA, blocked_until),
            "en": LanguageResult(1, StopReason.TARGET_REACHED),
        },
    )

    result = _produce(tmp_path, {"ru": 1, "en": 1})

    assert calls == [("ru", 1)]
    assert result.produced_by_language == {"ru": 0, "en": 0}
    assert result.produced_total == 0
    assert result.stop_reason is StopReason.CAPTCHA
    assert result.blocked_until == blocked_until


def test_duration_exceeded_stops_remaining_languages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "McpClient", FakeMcp)
    calls = _patch_run_language(
        monkeypatch,
        {
            "ru": LanguageResult(0, StopReason.DURATION_EXCEEDED),
            "en": LanguageResult(1, StopReason.TARGET_REACHED),
        },
    )

    result = _produce(tmp_path, {"ru": 1, "en": 1})

    assert calls == [("ru", 1)]
    assert result.produced_by_language == {"ru": 0, "en": 0}
    assert result.stop_reason is StopReason.DURATION_EXCEEDED


def test_deadline_check_between_languages_stops_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "McpClient", FakeMcp)
    calls = _patch_run_language(
        monkeypatch,
        {
            "ru": LanguageResult(1, StopReason.TARGET_REACHED),
            "en": LanguageResult(1, StopReason.TARGET_REACHED),
        },
    )
    clock = iter([0.0, 0.0, 1_000.0])
    monkeypatch.setattr(service.time, "monotonic", lambda: next(clock))

    result = _produce(tmp_path, {"ru": 1, "en": 1})

    assert calls == [("ru", 1)]
    assert result.produced_by_language == {"ru": 1, "en": 0}
    assert result.stop_reason is StopReason.DURATION_EXCEEDED


def test_language_without_queries_returns_no_queries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "McpClient", FakeMcp)
    calls: list[tuple[str, list[object], bool]] = []

    def fake_run(
        cfg: object,
        *,
        repo: object,
        mcp: object,
        queries: object,
        target: int,
        budget: object,
        deadline: float,
        no_queries: bool = False,
    ) -> LanguageResult:
        del repo, mcp, target, budget, deadline
        calls.append((cfg.language, list(queries), no_queries))
        return LanguageResult(0, StopReason.NO_QUERIES)

    monkeypatch.setattr(service, "run_language", fake_run)

    result = _produce(tmp_path, {"de": 1})

    assert calls == [("de", [], True)]
    assert result.produced_by_language == {"de": 0}
    assert result.stop_reason is StopReason.NO_QUERIES
    assert (tmp_path / CONTENT_DIR / "de").is_dir()


def test_invalid_language_is_rejected_before_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        service,
        "McpClient",
        lambda *args, **kwargs: pytest.fail("MCP must not be used"),
    )

    with pytest.raises(SupplierError, match="недопустимый код языка"):
        _produce(tmp_path, {"../x": 1})

    assert not (tmp_path / CONTENT_DIR).exists()


def test_empty_query_file_drains_approved_leftovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "McpClient", FakeMcp)
    video = make_video("444", "dmitri", language="de")
    repo = Repository(tmp_path / DB_DIR / DB_FILENAME)
    repo.init_schema()
    assert repo.add_found(video)
    repo.save_decision(video, make_metadata(video), FilterDecision(True))
    queries_dir = tmp_path / QUERIES_DIR
    queries_dir.mkdir(parents=True)
    (queries_dir / "de.txt").write_text("# empty list\n", encoding="utf-8")
    payload = b"de-video"

    monkeypatch.setattr(
        "content_factory.services.supplier.filter.fetch_video_metadata",
        lambda url, proxy="", timeout=30: make_metadata(video),
    )

    class FakeYDL:
        def __init__(self, opts: dict[str, object]) -> None:
            self.opts = opts

        def __enter__(self) -> FakeYDL:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def extract_info(self, url: str, download: bool = True) -> dict[str, object]:
            del url, download
            Path(str(self.opts["outtmpl"])).write_bytes(payload)
            return {"ext": "mp4", "vcodec": "h264"}

    monkeypatch.setattr(downloader, "YoutubeDL", FakeYDL)

    result = _produce(tmp_path, {"de": 1})

    sha = hashlib.sha256(payload).hexdigest()
    assert result.produced_by_language == {"de": 1}
    assert result.stop_reason is StopReason.TARGET_REACHED
    assert (tmp_path / CONTENT_DIR / "de" / f"{sha}.mp4").read_bytes() == payload


def test_run_budget_is_shared_between_languages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_browser(monkeypatch, {RU_URL: b"ru-video", EN_URL: b"en-video"})

    result = _produce(
        tmp_path,
        {"ru": 1, "en": 1},
        runtime=make_runtime(search=SearchConfig(max_queries=8, pause=0.0)),
    )

    assert result.produced_by_language == {"ru": 1, "en": 1}
    assert result.produced_total == 2
    assert result.stop_reason is StopReason.TARGET_REACHED


def test_stale_tmp_files_are_swept(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "McpClient", FakeMcp)
    _patch_run_language(
        monkeypatch,
        {"ru": LanguageResult(0, StopReason.NO_PROGRESS)},
    )
    content = tmp_path / CONTENT_DIR / "ru"
    content.mkdir(parents=True)
    stale = content / "old.tmp"
    fresh = content / "new.tmp"
    stale.write_bytes(b"old")
    fresh.write_bytes(b"new")
    old_time = time.time() - 2 * 24 * 60 * 60
    os.utime(stale, (old_time, old_time))
    tmp_dir = content / ".tmp"
    tmp_dir.mkdir()
    stale_part = tmp_dir / "111.part"
    stale_part.write_bytes(b"part")
    os.utime(stale_part, (old_time, old_time))

    _produce(tmp_path, {"ru": 1})

    assert not stale.exists()
    assert not stale_part.exists()
    assert fresh.exists()


def test_stale_tmp_sweep_skips_tmp_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = tmp_path / "content"
    tmp_dir = content / ".tmp"
    tmp_dir.mkdir(parents=True)
    old_time = time.time() - 2 * 24 * 60 * 60
    os.utime(tmp_dir, (old_time, old_time))
    removed: list[Path] = []
    real_unlink = Path.unlink

    def record(self: Path, *args: object, **kwargs: object) -> None:
        removed.append(self)
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", record)

    service._sweep_stale_tmp(content)

    assert tmp_dir not in removed


def test_stale_tmp_sweep_logs_filesystem_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = tmp_path / "content"
    content.mkdir()
    stale = content / "old.tmp"
    stale.write_bytes(b"old")
    old_time = time.time() - 2 * 24 * 60 * 60
    os.utime(stale, (old_time, old_time))

    def fail(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        raise OSError("denied")

    monkeypatch.setattr(Path, "stat", fail)

    service._sweep_stale_tmp(content)

    assert stale in set(content.iterdir())


def test_stale_tmp_sweep_logs_listing_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(self: Path, *args: object, **kwargs: object) -> object:
        raise OSError("denied")

    monkeypatch.setattr(Path, "glob", fail)

    service._sweep_stale_tmp(tmp_path)


def test_pause_between_languages_is_used(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "McpClient", FakeMcp)
    _patch_run_language(
        monkeypatch,
        {
            "ru": LanguageResult(1, StopReason.TARGET_REACHED),
            "en": LanguageResult(1, StopReason.TARGET_REACHED),
        },
    )
    pauses: list[tuple[float, float]] = []
    monkeypatch.setattr(
        service,
        "_pause_between",
        lambda seconds, deadline: pauses.append((seconds, deadline)),
    )

    _produce(
        tmp_path,
        {"ru": 1, "en": 1},
        runtime=make_runtime(
            run=RunConfig(
                batch=20,
                interval=900.0,
                max_duration=600.0,
                pause_between_languages=2.5,
            )
        ),
    )

    assert len(pauses) == 1
    assert pauses[0][0] == 2.5


def test_profile_is_kept_open_when_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "McpClient", FakeMcp)
    _patch_run_language(
        monkeypatch,
        {"ru": LanguageResult(1, StopReason.TARGET_REACHED)},
    )

    _produce(
        tmp_path,
        {"ru": 1},
        supplier_settings=make_supplier_settings(close_profile_after_crawl=False),
    )

    assert FakeMcp.instances[0].count("close_profile") == 0


def test_close_error_is_swallowed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RaisingCloseMcp(FakeMcp):
        def close_profile(self, profile_id: str) -> None:
            raise RuntimeError("close failed")

    monkeypatch.setattr(service, "McpClient", RaisingCloseMcp)
    _patch_run_language(
        monkeypatch,
        {"ru": LanguageResult(1, StopReason.TARGET_REACHED)},
    )

    result = _produce(tmp_path, {"ru": 1})

    assert result.produced_total == 1


def test_open_error_propagates_without_closing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RaisingOpenMcp(FakeMcp):
        def open_profile(self, profile_id: str, headless: bool = False) -> None:
            raise SupplierError("open failed")

    monkeypatch.setattr(service, "McpClient", RaisingOpenMcp)

    with pytest.raises(SupplierError, match="open failed"):
        _produce(tmp_path, {"ru": 1})

    assert FakeMcp.instances[0].count("close_profile") == 0


def test_seed_queries_reports_filesystem_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(service.shutil, "copyfileobj", fail)

    with pytest.raises(SupplierError, match="не удалось создать файл запросов"):
        service._seed_queries(tmp_path / "queries", "ru")

    assert not (tmp_path / "queries" / "ru.txt").exists()
    assert list((tmp_path / "queries").glob(".*.tmp")) == []


def test_seed_queries_ignores_a_race(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def racing_link(*args: object, **kwargs: object) -> None:
        raise FileExistsError

    monkeypatch.setattr(service.os, "link", racing_link)

    service._seed_queries(tmp_path / "queries", "ru")

    assert not (tmp_path / "queries" / "ru.txt").exists()
    assert list((tmp_path / "queries").glob(".*.tmp")) == []


def test_seed_queries_wraps_tempfile_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("no space")

    monkeypatch.setattr(service.tempfile, "mkstemp", fail)

    with pytest.raises(SupplierError, match="не удалось создать файл запросов"):
        service._seed_queries(tmp_path / "queries", "ru")


def test_seed_queries_falls_back_without_hardlinks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unsupported(*args: object, **kwargs: object) -> None:
        raise OSError("hard links are not supported")

    monkeypatch.setattr(service.os, "link", unsupported)
    queries_dir = tmp_path / "queries"

    service._seed_queries(queries_dir, "ru")

    seed = (service._SEED_QUERIES_DIR / "ru.txt").read_text(encoding="utf-8")
    assert (queries_dir / "ru.txt").read_text(encoding="utf-8") == seed
    assert list(queries_dir.glob(".*.tmp")) == []


def test_copy_without_overwrite_ignores_existing_file(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.write_bytes(b"new")
    destination.write_bytes(b"old")

    service._copy_without_overwrite(source, destination)

    assert destination.read_bytes() == b"old"


def test_copy_without_overwrite_removes_partial_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.write_bytes(b"new")

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(service.shutil, "copyfileobj", fail)

    with pytest.raises(OSError, match="disk full"):
        service._copy_without_overwrite(source, destination)

    assert not destination.exists()


def test_ensure_directory_reports_filesystem_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("denied")

    monkeypatch.setattr(Path, "mkdir", fail)

    with pytest.raises(SupplierError, match="не удалось создать каталог"):
        service._ensure_directory(tmp_path / "content")


def test_pause_between_respects_zero_and_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []
    monkeypatch.setattr(service.time, "sleep", slept.append)

    service._pause_between(0, service.time.monotonic() + 10)
    service._pause_between(5, service.time.monotonic() - 1)
    service._pause_between(7, service.time.monotonic() + 100)

    assert slept == [7]


@pytest.mark.parametrize(
    ("produced", "results", "total_plan", "reason", "blocked"),
    [
        (
            {"ru": 1},
            [LanguageResult(1, StopReason.CAPTCHA, "until")],
            2,
            StopReason.CAPTCHA,
            "until",
        ),
        (
            {"ru": 1},
            [LanguageResult(1, StopReason.DURATION_EXCEEDED)],
            2,
            StopReason.DURATION_EXCEEDED,
            None,
        ),
        (
            {"ru": 2},
            [LanguageResult(2, StopReason.STOCK_SHORTFALL)],
            2,
            StopReason.TARGET_REACHED,
            None,
        ),
        (
            {"ru": 0},
            [LanguageResult(0, StopReason.NO_QUERIES)],
            1,
            StopReason.NO_QUERIES,
            None,
        ),
        (
            {"ru": 0},
            [LanguageResult(0, StopReason.NO_PROGRESS)],
            1,
            StopReason.NO_PROGRESS,
            None,
        ),
        (
            {"ru": 1, "en": 0},
            [
                LanguageResult(1, StopReason.TARGET_REACHED),
                LanguageResult(0, StopReason.NO_QUERIES),
            ],
            3,
            StopReason.NO_QUERIES,
            None,
        ),
        (
            {"ru": 0, "en": 0},
            [
                LanguageResult(0, StopReason.NO_QUERIES),
                LanguageResult(0, StopReason.NO_PROGRESS),
            ],
            2,
            StopReason.NO_QUERIES,
            None,
        ),
        (
            {"ru": 0},
            [LanguageResult(0, StopReason.STOCK_SHORTFALL)],
            1,
            StopReason.STOCK_SHORTFALL,
            None,
        ),
        ({}, [], 0, StopReason.ENOUGH_STOCK, None),
    ],
)
def test_result_aggregation(
    produced: dict[str, int],
    results: list[LanguageResult],
    total_plan: int,
    reason: StopReason,
    blocked: str | None,
) -> None:
    result = service._result(produced, results, total_plan)

    assert result.produced_by_language == produced
    assert result.produced_total == sum(produced.values())
    assert result.stop_reason is reason
    assert result.blocked_until == blocked


def _patch_browser(
    monkeypatch: pytest.MonkeyPatch,
    payloads: dict[str, bytes],
) -> None:
    monkeypatch.setattr(service, "McpClient", FakeMcp)
    monkeypatch.setattr(finder, "_pause", lambda *args, **kwargs: None)
    monkeypatch.setattr(finder, "_sleep_human", lambda *args, **kwargs: None)

    videos = {
        RU_URL: make_metadata(make_video("111", "alice", language="ru")),
        EN_URL: make_metadata(make_video("222", "bob", language="en")),
        SECOND_RU_URL: make_metadata(make_video("333", "carol", language="ru")),
    }
    monkeypatch.setattr(
        "content_factory.services.supplier.filter.fetch_video_metadata",
        lambda url, proxy="", timeout=30: videos[url],
    )

    class FakeYDL:
        def __init__(self, opts: dict[str, object]) -> None:
            self.opts = opts

        def __enter__(self) -> FakeYDL:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def extract_info(self, url: str, download: bool = True) -> dict[str, object]:
            del download
            Path(str(self.opts["outtmpl"])).write_bytes(payloads[url])
            return {"ext": "mp4", "vcodec": "h264"}

    monkeypatch.setattr(downloader, "YoutubeDL", FakeYDL)


def test_produce_end_to_end_downloads_video(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"ru-video-bytes"
    _patch_browser(monkeypatch, {RU_URL: payload})

    result = _produce(
        tmp_path,
        {"ru": 1},
        runtime=make_runtime(),
    )

    sha = hashlib.sha256(payload).hexdigest()
    content = tmp_path / CONTENT_DIR / "ru"
    assert result.produced_by_language == {"ru": 1}
    assert result.produced_total == 1
    assert result.stop_reason is StopReason.TARGET_REACHED
    assert (content / f"{sha}.mp4").read_bytes() == payload
    assert [path.name for path in content.iterdir() if path.suffix == ".mp4"] == [f"{sha}.mp4"]
    repo = Repository(tmp_path / DB_DIR / DB_FILENAME)
    assert repo.downloaded_count("ru") == 1
    assert repo.downloaded_count("en") == 0
    assert (tmp_path / QUERIES_DIR / "ru.txt").is_file()
    assert FakeMcp.instances[0].count("open_profile") == 1
    assert FakeMcp.instances[0].count("close_profile") == 1


def test_produce_end_to_end_isolates_languages(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ru_payload = b"ru-video"
    en_payload = b"en-video"
    _patch_browser(monkeypatch, {RU_URL: ru_payload, EN_URL: en_payload})

    result = _produce(
        tmp_path,
        {"ru": 1, "en": 1},
        runtime=make_runtime(),
    )

    ru_sha = hashlib.sha256(ru_payload).hexdigest()
    en_sha = hashlib.sha256(en_payload).hexdigest()
    ru_dir = tmp_path / CONTENT_DIR / "ru"
    en_dir = tmp_path / CONTENT_DIR / "en"
    assert result.produced_by_language == {"ru": 1, "en": 1}
    assert result.produced_total == 2
    assert result.stop_reason is StopReason.TARGET_REACHED
    assert (ru_dir / f"{ru_sha}.mp4").read_bytes() == ru_payload
    assert (en_dir / f"{en_sha}.mp4").read_bytes() == en_payload
    assert not (ru_dir / f"{en_sha}.mp4").exists()
    assert not (en_dir / f"{ru_sha}.mp4").exists()
    repo = Repository(tmp_path / DB_DIR / DB_FILENAME)
    assert repo.downloaded_count("ru") == 1
    assert repo.downloaded_count("en") == 1
    assert (tmp_path / QUERIES_DIR / "ru.txt").is_file()
    assert (tmp_path / QUERIES_DIR / "en.txt").is_file()
    assert FakeMcp.instances[0].count("open_profile") == 1
    assert FakeMcp.instances[0].count("close_profile") == 1


def test_repeated_search_with_identical_payload_keeps_one_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"same-search-result"
    _patch_browser(monkeypatch, {RU_URL: payload})

    first = _produce(tmp_path, {"ru": 1})
    second = _produce(tmp_path, {"ru": 1})

    sha = hashlib.sha256(payload).hexdigest()
    content = tmp_path / CONTENT_DIR / "ru"
    assert first.produced_total == 1
    assert second.produced_total == 0
    assert second.stop_reason is StopReason.QUERIES_EXHAUSTED
    assert [path.name for path in content.glob("*.mp4")] == [f"{sha}.mp4"]
    repo = Repository(tmp_path / DB_DIR / DB_FILENAME)
    assert repo.downloaded_count("ru") == 1


def test_new_video_with_identical_sha_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SecondVideoMcp(FakeMcp):
        ru_url = SECOND_RU_URL
        ru_dom = SECOND_RU_DOM

    payload = b"identical-video-bytes"
    _patch_browser(monkeypatch, {RU_URL: payload, SECOND_RU_URL: payload})
    first = _produce(tmp_path, {"ru": 1})

    monkeypatch.setattr(service, "McpClient", SecondVideoMcp)
    second = _produce(tmp_path, {"ru": 1})

    sha = hashlib.sha256(payload).hexdigest()
    content = tmp_path / CONTENT_DIR / "ru"
    assert first.produced_total == 1
    assert second.produced_total == 0
    assert [path.name for path in content.glob("*.mp4")] == [f"{sha}.mp4"]
    repo = Repository(tmp_path / DB_DIR / DB_FILENAME)
    assert repo.downloaded_count("ru") == 1
    assert [video.video_id for video in repo.list_videos(STATUS_DOWNLOADED, language="ru")] == [
        "111"
    ]
    assert [video.video_id for video in repo.list_videos(STATUS_REJECTED, language="ru")] == ["333"]
