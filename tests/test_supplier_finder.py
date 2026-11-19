from __future__ import annotations

import random
import time
from pathlib import Path

import pytest

from content_factory.exceptions.errors import SupplierError
from content_factory.schemas.supplier.search import QuerySpec
from content_factory.schemas.supplier.settings import SupplierConfig
from content_factory.schemas.supplier.video import Video
from content_factory.services.supplier.finder import (
    Finder,
    _blocked_until,
    _is_captcha_page,
    _normalize_url,
    _sleep_human,
    find_videos,
    parse_search_dom,
    parse_video_url,
)
from supplier_helpers import make_config

VIDEO_URL = "https://www.tiktok.com/@alice/video/111"
SECOND_VIDEO_URL = "https://www.tiktok.com/@bob/video/222"
PHOTO_URL = "https://www.tiktok.com/@carol/photo/333"

SEARCH_DOM = """
<div class="grid" [r1]
  <a class="AVideoContainer" href="https://www.tiktok.com/@alice/video/111..." [r2]
  <strong class="video-count" [r3] "999K"
  <a class="AVideoContainer" href="https://www.tiktok.com/@bob/video/222..." [r4]
  <strong class="video-count" [r5] "5"
  <a class="AVideoContainer" href="https://www.tiktok.com/@carol/photo/333..." [r6]
  <strong class="video-count" [r7] "1"
"""

CAPTCHA_DOM = """
<div class="captcha" [r1]
  <h2>Verify to continue</h2>
  <p>Please complete the puzzle</p>
"""


def _cfg(**overrides: object) -> SupplierConfig:
    values: dict[str, object] = {
        "profile_id": "profile-1",
        "scrolls_min": 1,
        "scrolls_max": 1,
        "queries_per_session_min": 100,
        "queries_per_session_max": 100,
    }
    values.update(overrides)
    return make_config(Path(), **values)


def _query(name: str = "test query") -> QuerySpec:
    return QuerySpec(query=name, language="ru", source=Path("queries/ru.txt"))


class FakeSink:
    def __init__(
        self,
        existing: set[str] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.videos: list[Video] = []
        self._existing = set(existing or ())
        self._error = error

    def add_found(self, video: Video) -> bool:
        self.videos.append(video)
        if self._error is not None:
            raise self._error
        if video.video_id in self._existing:
            return False
        self._existing.add(video.video_id)
        return True


class FakeMcp:
    def __init__(
        self,
        doms: list[str] | None = None,
        hrefs: dict[str, str | None] | None = None,
        wait_error: Exception | None = None,
        navigate_errors: list[Exception | None] | None = None,
        dom_errors: list[Exception | None] | None = None,
        scroll_error: Exception | None = None,
        attr_error: Exception | None = None,
        human_error: Exception | None = None,
        url_value: str = "",
        url_error: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[object, ...]] = []
        self._doms = list(doms or [SEARCH_DOM])
        self._dom_index = 0
        self._hrefs = hrefs or {
            "r2": VIDEO_URL,
            "r4": SECOND_VIDEO_URL,
            "r6": PHOTO_URL,
        }
        self._wait_error = wait_error
        self._navigate_errors = list(navigate_errors or [])
        self._dom_errors = list(dom_errors or [])
        self._scroll_error = scroll_error
        self._attr_error = attr_error
        self._human_error = human_error
        self._url = url_value
        self._url_error = url_error

    def _next_dom(self) -> str:
        index = min(self._dom_index, len(self._doms) - 1)
        self._dom_index += 1
        return self._doms[index]

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
        self.calls.append(("navigate", url))
        if self._navigate_errors:
            error = self._navigate_errors.pop(0)
            if error is not None:
                raise error
        if not self._url:
            self._url = url

    def wait_for_selector(
        self,
        selector: str,
        timeout_ms: int | None = None,
    ) -> None:
        self.calls.append(("wait_for_selector", selector))
        if self._wait_error is not None:
            raise self._wait_error

    def get_dom(self, **kwargs: object) -> str:
        self.calls.append(("get_dom",))
        if self._dom_errors:
            error = self._dom_errors.pop(0)
            if error is not None:
                raise error
        return self._next_dom()

    def get_attribute(
        self,
        attribute: str,
        ref: str | None = None,
        selector: str | None = None,
    ) -> str | None:
        self.calls.append(("get_attribute", ref, attribute))
        if self._attr_error is not None:
            raise self._attr_error
        if ref is None:
            return None
        return self._hrefs.get(ref)

    def scroll(self, direction: str = "down", amount: int = 700) -> None:
        self.calls.append(("scroll", direction, amount))
        if self._scroll_error is not None:
            raise self._scroll_error

    def human_idle(self, duration_ms: int) -> None:
        self.calls.append(("human_idle", duration_ms))
        if self._human_error is not None:
            raise self._human_error

    def url(self) -> str:
        if self._url_error is not None:
            raise self._url_error
        return self._url

    def count(self, name: str) -> int:
        return sum(1 for call in self.calls if call[0] == name)


def test_parse_search_dom_keeps_cards_and_ignores_like_counts() -> None:
    cards = parse_search_dom(SEARCH_DOM)

    assert [card.ref for card in cards] == ["r2", "r4", "r6"]
    assert cards[0].is_video is True
    assert cards[2].is_video is False


def test_parse_search_dom_flushes_card_without_count() -> None:
    dom = """
    <a class="AVideoContainer" href="https://www.tiktok.com/@alice/video/111..." [r2]
    <a class="AVideoContainer" href="https://www.tiktok.com/@bob/video/222..." [r4]
    """
    cards = parse_search_dom(dom)

    assert [card.ref for card in cards] == ["r2", "r4"]


def test_parse_search_dom_skips_cards_without_reference() -> None:
    dom = """
    <a class="AVideoContainer" href="https://www.tiktok.com/@alice/video/111..." [e1]
    <a class="AVideoContainer" [r2]
    """

    assert parse_search_dom(dom) == []


def test_parse_video_url_and_normalize_url() -> None:
    assert parse_video_url(VIDEO_URL) == ("alice", "111")
    assert parse_video_url("https://www.tiktok.com/@alice/photo/111") is None
    assert parse_video_url("") is None
    assert _normalize_url("//www.tiktok.com/@alice/video/1") == (
        "https://www.tiktok.com/@alice/video/1"
    )
    assert _normalize_url("/@alice/video/1") == "https://www.tiktok.com/@alice/video/1"
    assert _normalize_url(VIDEO_URL) == VIDEO_URL


def test_find_videos_requires_profile_id() -> None:
    with pytest.raises(SupplierError, match="SUPPLIER_PROFILE_ID"):
        find_videos(_cfg(profile_id=""), [_query()], 10, FakeSink(), FakeMcp())


def test_find_videos_zero_limit_and_empty_queries_do_not_call_mcp() -> None:
    mcp = FakeMcp()
    assert find_videos(_cfg(), [_query()], 0, FakeSink(), mcp) == find_videos(
        _cfg(), [], 10, FakeSink(), mcp
    )
    assert mcp.calls == []


def test_find_videos_stores_feed_cards_without_profile_visits() -> None:
    mcp = FakeMcp()
    sink = FakeSink()

    result = find_videos(_cfg(scrolls_min=0, scrolls_max=0), [_query()], 10, sink, mcp)

    assert result.found == 2
    assert result.queries_run == 1
    assert result.links_seen == 2
    assert result.duplicates == 0
    assert result.errors == 0
    assert [video.video_id for video in sink.videos] == ["111", "222"]
    assert [video.author for video in sink.videos] == ["alice", "bob"]
    assert sink.videos[0].query == "test query"
    assert sink.videos[0].language == "ru"
    assert mcp.count("navigate") == 1
    assert mcp.count("scroll") == 0
    assert mcp.count("open_profile") == 0
    assert mcp.count("close_profile") == 0
    navigated = [call[1] for call in mcp.calls if call[0] == "navigate"]
    assert all("/@" not in str(url) for url in navigated)


def test_find_videos_deduplicates_inside_run_and_against_sink() -> None:
    mcp = FakeMcp(doms=[SEARCH_DOM, SEARCH_DOM, SEARCH_DOM])
    sink = FakeSink(existing={"111"})

    result = find_videos(_cfg(scrolls_min=2, scrolls_max=2), [_query()], 10, sink, mcp)

    assert result.found == 1
    assert result.duplicates == 5
    assert [video.video_id for video in sink.videos] == ["111", "222"]


def test_find_videos_respects_limit_before_scroll() -> None:
    mcp = FakeMcp()
    sink = FakeSink()

    result = find_videos(_cfg(), [_query()], 1, sink, mcp)

    assert result.found == 1
    assert [video.video_id for video in sink.videos] == ["111"]
    assert mcp.count("scroll") == 0


def test_find_videos_stops_on_captcha_after_wait_error() -> None:
    mcp = FakeMcp(doms=[CAPTCHA_DOM], wait_error=SupplierError("timeout"))
    result = find_videos(_cfg(), [_query()], 10, FakeSink(), mcp)

    assert result.captcha is True
    assert result.blocked_until is not None
    assert result.queries_run == 1


def test_find_videos_keeps_partial_stats_when_captcha_arrives_later() -> None:
    mcp = FakeMcp(doms=[SEARCH_DOM, CAPTCHA_DOM])
    sink = FakeSink()

    result = find_videos(_cfg(), [_query()], 10, sink, mcp)

    assert result.captcha is True
    assert result.found == 2
    assert result.links_seen == 2
    assert result.queries_run == 1
    assert [video.video_id for video in sink.videos] == ["111", "222"]


def test_find_videos_stops_on_captcha_text_in_dom() -> None:
    mcp = FakeMcp(doms=[CAPTCHA_DOM])
    result = find_videos(_cfg(), [_query()], 10, FakeSink(), mcp)

    assert result.captcha is True
    assert result.blocked_until is not None


def test_find_videos_stops_on_captcha_url() -> None:
    mcp = FakeMcp(url_value="https://www.tiktok.com/verify")
    result = find_videos(_cfg(), [_query()], 10, FakeSink(), mcp)

    assert result.captcha is True


def test_find_videos_continues_after_navigate_error() -> None:
    mcp = FakeMcp(navigate_errors=[SupplierError("network"), None])
    sink = FakeSink()

    result = find_videos(_cfg(), [_query("first"), _query("second")], 10, sink, mcp)

    assert result.errors == 1
    assert result.queries_run == 2
    assert result.found == 2
    assert len(sink.videos) == 2


def test_find_videos_counts_dom_error() -> None:
    mcp = FakeMcp(dom_errors=[SupplierError("dom")])
    result = find_videos(_cfg(), [_query()], 10, FakeSink(), mcp)

    assert result.errors == 1
    assert result.found == 0


def test_find_videos_counts_scroll_error() -> None:
    mcp = FakeMcp(scroll_error=SupplierError("scroll"))
    result = find_videos(_cfg(), [_query()], 10, FakeSink(), mcp)

    assert result.errors == 1
    assert result.found == 2


def test_find_videos_counts_href_error() -> None:
    mcp = FakeMcp(attr_error=SupplierError("attribute"))
    result = find_videos(_cfg(scrolls_min=0, scrolls_max=0), [_query()], 10, FakeSink(), mcp)

    assert result.errors == 2
    assert result.found == 0


def test_find_videos_counts_invalid_href() -> None:
    mcp = FakeMcp(hrefs={"r2": "https://example.test/nope", "r4": None, "r6": PHOTO_URL})
    result = find_videos(_cfg(scrolls_min=0, scrolls_max=0), [_query()], 10, FakeSink(), mcp)

    assert result.errors == 2
    assert result.found == 0


def test_find_videos_counts_sink_error() -> None:
    sink = FakeSink(error=SupplierError("db"))
    result = find_videos(_cfg(), [_query()], 10, sink, FakeMcp())

    assert result.errors == 2
    assert result.found == 0


def test_find_videos_uses_session_break(monkeypatch: pytest.MonkeyPatch) -> None:
    pauses: list[tuple[float, float]] = []

    def fake_pause(session: object, minimum: float, maximum: float) -> None:
        pauses.append((minimum, maximum))

    monkeypatch.setattr("content_factory.services.supplier.finder._pause", fake_pause)

    mcp = FakeMcp()
    result = find_videos(
        _cfg(
            queries_per_session_min=1,
            queries_per_session_max=1,
            session_break_min=7,
            session_break_max=7,
        ),
        [_query("first"), _query("second")],
        10,
        FakeSink(),
        mcp,
    )

    assert result.queries_run == 2
    assert (7, 7) in pauses


def test_find_videos_uses_extra_pause_after_query(monkeypatch: pytest.MonkeyPatch) -> None:
    pauses: list[tuple[float, float]] = []

    def fake_pause(session: object, minimum: float, maximum: float) -> None:
        pauses.append((minimum, maximum))

    monkeypatch.setattr("content_factory.services.supplier.finder._pause", fake_pause)
    monkeypatch.setattr(random, "random", lambda: 0.0)

    find_videos(
        _cfg(queries_per_session_min=100, queries_per_session_max=100),
        [_query("first"), _query("second")],
        10,
        FakeSink(),
        FakeMcp(),
    )

    assert (20.0, 40.0) in pauses


def test_sleep_human_falls_back_to_sleep_on_mcp_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []
    monkeypatch.setattr(time, "sleep", slept.append)

    _sleep_human(FakeMcp(human_error=SupplierError("idle")), 0.01)

    assert slept


def test_is_captcha_page_uses_url_and_dom() -> None:
    assert _is_captcha_page(FakeMcp(url_value="https://www.tiktok.com/verify")) is True
    assert _is_captcha_page(FakeMcp(doms=[CAPTCHA_DOM])) is True
    assert _is_captcha_page(FakeMcp()) is False


def test_blocked_until_is_iso_timestamp() -> None:
    value = _blocked_until(_cfg(captcha_cooldown_min=1, captcha_cooldown_max=1))

    assert "T" in value
    assert value.endswith("+00:00")


def test_finder_stage_delegates_to_find_videos() -> None:
    sink = FakeSink()
    stage = Finder(_cfg(), sink, FakeMcp())

    result = stage.find([_query()], 10)

    assert result.found == 2
    assert result.queries_run == 1
    assert [video.video_id for video in sink.videos] == ["111", "222"]


def test_find_videos_reports_page_without_cards() -> None:
    mcp = FakeMcp(wait_error=SupplierError("timeout"))

    result = find_videos(_cfg(), [_query()], 10, FakeSink(), mcp)

    assert result.errors == 1
    assert result.found == 0
    assert result.captcha is False


def test_is_captcha_page_handles_url_error() -> None:
    mcp = FakeMcp(doms=[CAPTCHA_DOM], url_error=SupplierError("url"))

    assert _is_captcha_page(mcp) is True


def test_find_videos_survives_url_error_inside_dom_loop() -> None:
    mcp = FakeMcp(url_error=SupplierError("url"))
    sink = FakeSink()

    result = find_videos(_cfg(scrolls_min=0, scrolls_max=0), [_query()], 10, sink, mcp)

    assert result.found == 2
    assert result.errors == 0
    assert [video.video_id for video in sink.videos] == ["111", "222"]


def test_find_videos_counts_crawl_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> object:
        raise SupplierError("crawl failed")

    monkeypatch.setattr("content_factory.services.supplier.finder._crawl_query", fail)

    result = find_videos(_cfg(), [_query()], 10, FakeSink(), FakeMcp())

    assert result.errors == 1
    assert result.queries_run == 1
    assert result.found == 0
