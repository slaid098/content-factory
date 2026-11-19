"""Поиск видео TikTok в ленте через действия BitBrowser MCP."""

from __future__ import annotations

import random
import re
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from loguru import logger

from content_factory.exceptions.errors import SupplierError
from content_factory.schemas.supplier.ports import FoundSink
from content_factory.schemas.supplier.progress import FindTotals, QueryStats
from content_factory.schemas.supplier.results import FindResult
from content_factory.schemas.supplier.search import QuerySpec, SearchCard
from content_factory.schemas.supplier.settings import SupplierConfig
from content_factory.schemas.supplier.video import Video
from content_factory.services.supplier.mcp import McpClient
from content_factory.services.supplier.mcp_session import McpSession

_SEARCH_URL = "https://www.tiktok.com/search?q={query}"
_CARD_SELECTOR = 'a[href*="/video/"]'
_CAPTCHA_EXTRA_PAUSE_CHANCE = 0.15
_CAPTCHA_EXTRA_PAUSE_MIN = 20.0
_CAPTCHA_EXTRA_PAUSE_MAX = 40.0
_HUMAN_IDLE_CHUNK_MIN = 8.0
_HUMAN_IDLE_CHUNK_MAX = 14.0

_CAPTCHA_TEXT_MARKERS = (
    "verify to continue",
    "verify you are human",
    "подтвердите, что вы не робот",
    "пройдите проверку",
    "подтвердите свою личность",
)
_CAPTCHA_URL_MARKERS = ("/verify", "captcha")

_HREF_RE = re.compile(r'href="([^"]*)"')
_REF_RE = re.compile(r"\[(r\d+)\]")
_VIDEO_URL_RE = re.compile(r"https?://(?:www\.)?tiktok\.com/@([^/?#]+)/video/(\d+)")


class _CaptchaDetected(Exception):
    """Internal signal: TikTok asked for human verification."""

    def __init__(self, stats: QueryStats) -> None:
        super().__init__("captcha")
        self.stats = stats


def parse_search_dom(dom: str) -> list[SearchCard]:
    """Разобрать карточки видео из упрощённого DOM поиска TikTok.

    Каждая ссылка ``AVideoContainer`` — карточка. Строка ``video-count``
    закрывает карточку, но её число относится к лайкам и игнорируется.

    Args:
        dom: Упрощённый DOM, который вернуло действие MCP ``dom``.

    Returns:
        Карточки в порядке DOM.
    """
    cards: list[SearchCard] = []
    pending: tuple[str, str] | None = None

    def flush() -> None:
        nonlocal pending
        if pending is None:
            return
        href, ref = pending
        cards.append(
            SearchCard(
                href_prefix=href,
                ref=ref,
                is_video="/video/" in href,
            )
        )
        pending = None

    for line in dom.splitlines():
        if "AVideoContainer" in line and "href=" in line:
            flush()
            href_match = _HREF_RE.search(line)
            ref_match = _REF_RE.search(line)
            if href_match is not None and ref_match is not None:
                pending = (href_match.group(1), ref_match.group(1))
            continue
        if pending is not None and "video-count" in line:
            flush()

    flush()
    return cards


def _normalize_url(url: str) -> str:
    value = url.strip()
    if value.startswith("//"):
        return f"https:{value}"
    if value.startswith("/"):
        return f"https://www.tiktok.com{value}"
    return value


def parse_video_url(url: str) -> tuple[str, str] | None:
    """Вернуть ``(author, video_id)`` из ссылки на видео TikTok."""
    match = _VIDEO_URL_RE.search(url.strip())
    if match is None:
        return None
    return match.group(1), match.group(2)


def _looks_like_captcha_text(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _CAPTCHA_TEXT_MARKERS)


def _looks_like_captcha_url(url: str) -> bool:
    lowered = url.lower()
    return any(marker in lowered for marker in _CAPTCHA_URL_MARKERS)


def _current_url(session: McpSession) -> str:
    try:
        return session.url()
    except SupplierError:
        return ""


def _is_captcha_page(session: McpSession) -> bool:
    if _looks_like_captcha_url(_current_url(session)):
        return True
    dom = session.get_dom(
        max_depth=10,
        max_children=50,
        max_output_chars=30_000,
        text_max_length=200,
    )
    return _looks_like_captcha_text(dom)


def _sleep_human(session: McpSession, seconds: float) -> None:
    remaining = max(0.0, seconds)
    while remaining > 0:
        chunk = min(remaining, random.uniform(_HUMAN_IDLE_CHUNK_MIN, _HUMAN_IDLE_CHUNK_MAX))
        try:
            session.human_idle(max(1, int(chunk * 1000)))
        except SupplierError:
            time.sleep(chunk)
        remaining -= chunk


def _pause(
    session: McpSession,
    minimum: float,
    maximum: float,
) -> None:
    _sleep_human(session, random.uniform(minimum, maximum))


def _blocked_until(cfg: SupplierConfig) -> str:
    seconds = random.randint(cfg.captcha_cooldown_min, cfg.captcha_cooldown_max)
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def _resolve_ref(session: McpSession, ref: str) -> str | None:
    value = session.get_attribute("href", ref=ref)
    if not value:
        return None
    return _normalize_url(value)


def _store_card(
    card: SearchCard,
    spec: QuerySpec,
    cfg: SupplierConfig,
    session: McpSession,
    seen: set[str],
    sink: FoundSink,
) -> str:
    try:
        full_url = _resolve_ref(session, card.ref)
    except SupplierError as ex:
        logger.bind(error=ex, error_type=type(ex).__name__, query=spec.query).warning(
            "Не удалось прочитать ссылку карточки по запросу «{}»", spec.query
        )
        return "error"
    parsed = parse_video_url(full_url or "")
    if parsed is None:
        return "error"

    author, video_id = parsed
    if video_id in seen:
        return "duplicate"
    seen.add(video_id)

    video = Video(
        video_id=video_id,
        url=f"https://www.tiktok.com/@{author}/video/{video_id}",
        author=author,
        query=spec.query,
        language=cfg.language,
    )
    try:
        inserted = sink.add_found(video)
    except SupplierError as ex:
        logger.bind(error=ex, error_type=type(ex).__name__, video_id=video_id).error(
            "Не удалось сохранить видео {}", video_id
        )
        return "error"
    return "found" if inserted else "duplicate"


def _open_search(session: McpSession, cfg: SupplierConfig, spec: QuerySpec) -> bool:
    url = _SEARCH_URL.format(query=quote(spec.query, safe=""))
    try:
        session.navigate(url, timeout_ms=45_000)
    except SupplierError as ex:
        logger.bind(error=ex, error_type=type(ex).__name__, query=spec.query).warning(
            "Не удалось открыть поиск по запросу «{}»", spec.query
        )
        return False
    _pause(session, cfg.page_settle_min, cfg.page_settle_max)
    return True


def _await_cards(session: McpSession, spec: QuerySpec, stats: QueryStats) -> bool:
    try:
        session.wait_for_selector(_CARD_SELECTOR, timeout_ms=20_000)
    except SupplierError:
        if _is_captcha_page(session):
            raise _CaptchaDetected(stats) from None
        stats.errors += 1
        logger.bind(query=spec.query).warning(
            "По запросу «{}» не появилось ни одной карточки", spec.query
        )
        return False
    return True


def _read_dom(session: McpSession, spec: QuerySpec, stats: QueryStats) -> str | None:
    try:
        return session.get_dom()
    except SupplierError as ex:
        stats.errors += 1
        logger.bind(error=ex, error_type=type(ex).__name__, query=spec.query).warning(
            "Не удалось прочитать выдачу по запросу «{}»", spec.query
        )
        return None


def _collect_cards(
    dom: str,
    cfg: SupplierConfig,
    spec: QuerySpec,
    session: McpSession,
    seen: set[str],
    sink: FoundSink,
    limit: int,
    stats: QueryStats,
) -> None:
    for card in parse_search_dom(dom):
        if not card.is_video or not card.ref:
            continue
        stats.links_seen += 1
        outcome = _store_card(card, spec, cfg, session, seen, sink)
        if outcome == "found":
            stats.found += 1
        elif outcome == "duplicate":
            stats.duplicates += 1
        else:
            stats.errors += 1
        if stats.found >= limit:
            break


def _scroll_down(
    session: McpSession,
    cfg: SupplierConfig,
    spec: QuerySpec,
    stats: QueryStats,
) -> bool:
    try:
        session.scroll("down", random.randint(600, 1100))
    except SupplierError as ex:
        stats.errors += 1
        logger.bind(error=ex, error_type=type(ex).__name__, query=spec.query).warning(
            "Не удалось прокрутить выдачу по запросу «{}»", spec.query
        )
        return False
    _pause(session, cfg.pause_scroll_min, cfg.pause_scroll_max)
    return True


def _crawl_query(
    cfg: SupplierConfig,
    session: McpSession,
    spec: QuerySpec,
    seen: set[str],
    sink: FoundSink,
    limit: int,
) -> QueryStats:
    stats = QueryStats()
    if not _open_search(session, cfg, spec):
        stats.errors += 1
        return stats
    if not _await_cards(session, spec, stats):
        return stats

    scrolls = random.randint(cfg.scrolls_min, cfg.scrolls_max)
    for index in range(scrolls + 1):
        dom = _read_dom(session, spec, stats)
        if dom is None:
            break
        if _looks_like_captcha_url(_current_url(session)) or _looks_like_captcha_text(dom):
            raise _CaptchaDetected(stats)

        _collect_cards(dom, cfg, spec, session, seen, sink, limit, stats)
        if stats.found >= limit:
            break
        if index < scrolls and not _scroll_down(session, cfg, spec, stats):
            break

    return stats


def _rest_between_queries(
    session: McpSession,
    cfg: SupplierConfig,
    count: int,
    size: int,
) -> tuple[int, int]:
    count += 1
    if count >= size:
        logger.bind(queries=count).info("Перерыв между сессиями: {} запросов позади", count)
        _pause(session, cfg.session_break_min, cfg.session_break_max)
        return 0, random.randint(cfg.queries_per_session_min, cfg.queries_per_session_max)
    _sleep_human(session, cfg.search_pause)
    if random.random() < _CAPTCHA_EXTRA_PAUSE_CHANCE:
        _pause(session, _CAPTCHA_EXTRA_PAUSE_MIN, _CAPTCHA_EXTRA_PAUSE_MAX)
    return count, size


def _crawl_one(
    cfg: SupplierConfig,
    session: McpSession,
    spec: QuerySpec,
    seen: set[str],
    sink: FoundSink,
    limit: int,
    totals: FindTotals,
) -> bool:
    """Crawl one query and absorb its counters; False means captcha stop."""
    try:
        stats = _crawl_query(cfg, session, spec, seen, sink, limit)
    except _CaptchaDetected as exc:
        totals.absorb(exc.stats)
        totals.captcha = True
        totals.blocked_until = _blocked_until(cfg)
        logger.bind(query=spec.query).warning(
            "TikTok показал капчу на запросе «{}»; finder остановлен", spec.query
        )
        return False
    except SupplierError as ex:
        totals.errors += 1
        totals.queries_run += 1
        logger.bind(error=ex, error_type=type(ex).__name__, query=spec.query).error(
            "Ошибка MCP на запросе «{}»", spec.query
        )
        return True

    totals.absorb(stats)
    logger.bind(query=spec.query).info(
        "Запрос «{}»: links={}, stored={}, dup={}, errors={}",
        spec.query,
        stats.links_seen,
        stats.found,
        stats.duplicates,
        stats.errors,
    )
    return True


def find_videos(
    cfg: SupplierConfig,
    queries: Sequence[QuerySpec],
    limit: int,
    sink: FoundSink,
    mcp: McpClient,
) -> FindResult:
    """Собрать видео из поисковой ленты TikTok.

    Профиль BitBrowser должен быть уже открыт вызывающим кодом: за один
    производственный прогон он открывается один раз на все языки.

    Args:
        cfg: Настройки MCP, профиля и пауз.
        queries: Поисковые запросы в порядке выполнения.
        limit: Максимум новых видео, которые нужно сохранить.
        sink: Получатель найденных видео.
        mcp: Открытый клиент BitBrowser MCP.

    Returns:
        Счётчики прогона и признак капчи.

    Raises:
        SupplierError: Профиль BitBrowser не настроен.
    """
    if not cfg.profile_id:
        raise SupplierError("SUPPLIER_PROFILE_ID не задан: укажите ID профиля BitBrowser")
    if limit <= 0 or not queries:
        return FindResult()

    session = mcp.session(cfg.profile_id)
    totals = FindTotals()
    seen: set[str] = set()
    session_size = random.randint(cfg.queries_per_session_min, cfg.queries_per_session_max)
    session_count = 0
    index = 0

    while index < len(queries) and totals.found < limit:
        spec = queries[index]
        index += 1
        if not _crawl_one(cfg, session, spec, seen, sink, limit - totals.found, totals):
            break
        if totals.found >= limit:
            break
        if index < len(queries):
            session_count, session_size = _rest_between_queries(
                session,
                cfg,
                session_count,
                session_size,
            )

    return FindResult(
        found=totals.found,
        duplicates=totals.duplicates,
        queries_run=totals.queries_run,
        links_seen=totals.links_seen,
        errors=totals.errors,
        captcha=totals.captcha,
        blocked_until=totals.blocked_until,
    )


class Finder:
    """Этап поиска: одна порция видео из ленты по списку запросов."""

    def __init__(
        self,
        cfg: SupplierConfig,
        sink: FoundSink,
        mcp: McpClient,
    ) -> None:
        """Создать этап поиска.

        Args:
            cfg: Настройки поставщика.
            sink: Получатель найденных видео.
            mcp: Открытый клиент BitBrowser MCP.
        """
        self._cfg = cfg
        self._sink = sink
        self._mcp = mcp

    def find(self, queries: Sequence[QuerySpec], limit: int) -> FindResult:
        """Выполнить одну порцию поиска.

        Args:
            queries: Поисковые запросы в порядке выполнения.
            limit: Максимум новых видео.

        Returns:
            Счётчики порции.
        """
        return find_videos(self._cfg, queries, limit, self._sink, self._mcp)
