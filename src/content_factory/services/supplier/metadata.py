"""Разбор метаданных страницы видео TikTok."""

from __future__ import annotations

import json
import re
from typing import Any

from curl_cffi import requests
from curl_cffi.requests import ProxySpec

from content_factory.exceptions.errors import SupplierError
from content_factory.schemas.supplier.video import VideoMetadata

_UNIVERSAL_DATA_RE = re.compile(
    r"""<script[^>]*id=["']__UNIVERSAL_DATA_FOR_REHYDRATION__["'][^>]*>(.*?)</script>""",
    re.S,
)


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    if isinstance(value, (int, float)):
        return value == 1
    return False


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def parse_video_page(html: str, url: str = "") -> VideoMetadata:
    """Разобрать метаданные из HTML-страницы видео TikTok.

    Args:
        html: Полный HTML страницы видео.
        url: Исходная ссылка, которая сохраняется в результате.

    Returns:
        Разобранные метаданные.

    Raises:
        SupplierError: Блок universal data отсутствует или повреждён.
    """
    match = _UNIVERSAL_DATA_RE.search(html)
    if match is None:
        raise SupplierError("страница TikTok не содержит блока universal data")
    try:
        data = json.loads(match.group(1))
    except (json.JSONDecodeError, TypeError) as ex:
        raise SupplierError("universal data страницы TikTok не является JSON") from ex
    if not isinstance(data, dict):
        raise SupplierError("universal data страницы TikTok не является объектом")

    scope = _as_dict(data.get("__DEFAULT_SCOPE__"))
    detail = _as_dict(scope.get("webapp.video-detail"))
    item_info = _as_dict(detail.get("itemInfo"))
    item = item_info.get("itemStruct")
    if isinstance(item, list):
        item = item[0] if item else None
    if not isinstance(item, dict):
        raise SupplierError("страница TikTok не содержит itemStruct")

    author = _as_dict(item.get("author"))
    stats = _as_dict(item.get("stats"))
    author_stats = _as_dict(item.get("authorStats"))
    video = _as_dict(item.get("video"))

    video_id = str(item.get("id") or video.get("id") or "")
    if not video_id:
        raise SupplierError("страница TikTok не содержит id видео")

    author_id = str(author.get("uniqueId") or author.get("id") or "")
    author_name = str(author.get("nickname") or author_id)

    return VideoMetadata(
        video_id=video_id,
        url=url,
        author_id=author_id,
        author_name=author_name,
        description=str(item.get("desc") or ""),
        views=_as_int(stats.get("playCount")),
        followers=_as_int(author_stats.get("followerCount")),
        duration=_as_int(video.get("duration")),
        is_aigc=_as_bool(item.get("IsAigc")),
        aigc_description=str(item.get("AIGCDescription") or "").strip(),
        width=_as_int(video.get("width")),
        height=_as_int(video.get("height")),
        create_time=_as_int(item.get("createTime")),
    )


def fetch_video_metadata(url: str, proxy: str = "", timeout: int = 30) -> VideoMetadata:
    """Скачать страницу видео TikTok и разобрать её метаданные.

    Args:
        url: Публичная ссылка на видео.
        proxy: Необязательный HTTP(S)-прокси.
        timeout: Таймаут запроса в секундах.

    Returns:
        Разобранные метаданные.

    Raises:
        SupplierError: Страницу не удалось скачать или разобрать.
    """
    proxies: ProxySpec | None = {"http": proxy, "https": proxy} if proxy else None
    try:
        response = requests.get(
            url,
            impersonate="chrome",
            proxies=proxies,
            timeout=timeout,
        )
    except Exception as ex:
        raise SupplierError("не удалось скачать страницу видео TikTok") from ex
    if response.status_code != 200:
        raise SupplierError(f"страница TikTok вернула HTTP {response.status_code}")
    return parse_video_page(response.text, url=url)
