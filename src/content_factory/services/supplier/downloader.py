"""Этап скачивания: получить одобренные видео через yt-dlp."""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from loguru import logger
from yt_dlp import YoutubeDL

from content_factory.exceptions.errors import SupplierError
from content_factory.schemas.supplier.ports import DownloadSink
from content_factory.schemas.supplier.results import DownloadResult
from content_factory.schemas.supplier.settings import SupplierConfig
from content_factory.schemas.supplier.video import STATUS_APPROVED, DownloadedVideo, Video
from content_factory.services.supplier.proxy import resolve_proxy
from content_factory.services.supplier.repository import Repository

_FORMAT = "best[format_id!*=download]/best"
_AUDIO_EXTS = frozenset({"aac", "m4a", "mp3", "ogg", "opus", "wav"})
_HASH_CHUNK = 1024 * 1024
# Один процесс производит один план, поэтому блокировка сериализует финальную
# проверку SHA-256 и переименование: два одинаковых файла не перетрут друг друга.
_FINALIZE_LOCK = threading.Lock()


class _YtDlpLogger:
    """Мост от протокола логгера yt-dlp к loguru."""

    def debug(self, message: str) -> None:
        """Записать отладочное сообщение yt-dlp."""
        if message.startswith("[debug] "):
            message = message[8:]
        logger.bind(source="yt-dlp").debug("{}", message)

    def info(self, message: str) -> None:
        """Записать информационное сообщение yt-dlp."""
        logger.bind(source="yt-dlp").debug("{}", message)

    def warning(self, message: str) -> None:
        """Записать предупреждение yt-dlp."""
        logger.bind(source="yt-dlp").warning("{}", message)

    def error(self, message: str) -> None:
        """Записать ошибку yt-dlp."""
        logger.bind(source="yt-dlp").error("{}", message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sanitize(text: str, proxy: str) -> str:
    if not proxy:
        return text
    sanitized = text.replace(proxy, "<proxy>")
    try:
        parts = urlsplit(proxy)
    except ValueError:
        return sanitized
    if parts.username is None:
        return sanitized
    credentials = f"{parts.username}:{parts.password or ''}@"
    sanitized = sanitized.replace(credentials, "***@")
    sanitized = sanitized.replace(parts.username, "***")
    if parts.password:
        sanitized = sanitized.replace(parts.password, "***")
    return sanitized


def _clean_error(error: BaseException, proxy: str) -> str:
    message = str(error).strip() or error.__class__.__name__
    if message.startswith("ERROR: "):
        message = message[7:]
    return _sanitize(message, proxy)


def _match_filter(info: dict[str, Any], *, incomplete: bool = False) -> str | None:
    del incomplete
    if info.get("_type") == "playlist":
        return "PLAYLIST: нужна ссылка на одно видео"
    if info.get("vcodec") == "none":
        return "PHOTO_POST: фото-пост или аудио не поддерживается"
    return None


def _download_opts(cfg: SupplierConfig, tmp_path: Path) -> dict[str, Any]:
    return {
        "format": _FORMAT,
        "noplaylist": True,
        "retries": 5,
        "socket_timeout": 30,
        "proxy": cfg.proxy or None,
        "outtmpl": str(tmp_path),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "overwrites": True,
        "logger": _YtDlpLogger(),
        "match_filter": _match_filter,
    }


def _cleanup_tmp(tmp_dir: Path, video_id: str) -> None:
    for path in tmp_dir.glob(f"{video_id}.*"):
        path.unlink(missing_ok=True)


def _fetch_to_tmp(video: Video, cfg: SupplierConfig) -> tuple[Path, str, int]:
    """Скачать видео в ``.tmp`` и переименовать в ``<sha256>.tmp``.

    Returns:
        Путь к промежуточному файлу, его SHA-256 и размер в байтах.

    Raises:
        SupplierError: yt-dlp не смог скачать видео или вернул не mp4.
    """
    content_dir = cfg.content_dir
    content_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = content_dir / ".tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    raw_path = tmp_dir / f"{video.video_id}.part"
    _cleanup_tmp(tmp_dir, video.video_id)

    try:
        with YoutubeDL(_download_opts(cfg, raw_path)) as ydl:
            info = ydl.extract_info(video.url, download=True)

        if info is None:
            raise SupplierError("yt-dlp не вернул информацию о видео")
        if info.get("_type") == "playlist":
            raise SupplierError("yt-dlp вернул плейлист вместо видео")

        ext = str(info.get("ext") or "").lower()
        if info.get("vcodec") == "none" or ext in _AUDIO_EXTS:
            raise SupplierError("yt-dlp вернул фото-пост или аудио")
        if ext and ext != "mp4":
            raise SupplierError(f"yt-dlp вернул формат {ext}, нужен mp4")
        if not raw_path.is_file() or raw_path.stat().st_size == 0:
            raise SupplierError("yt-dlp не создал видеофайл")

        sha256 = _sha256(raw_path)
        size = raw_path.stat().st_size
        staged = content_dir / f"{sha256}.tmp"
        raw_path.replace(staged)
    except SupplierError:
        _cleanup_tmp(tmp_dir, video.video_id)
        raise
    except Exception as ex:
        _cleanup_tmp(tmp_dir, video.video_id)
        raise SupplierError(_clean_error(ex, cfg.proxy)) from ex

    return staged, sha256, size


def _select_videos(videos: Sequence[Video], max_per_author: int) -> list[Video]:
    selected: list[Video] = []
    counts: dict[str, int] = {}
    for video in videos:
        count = counts.get(video.author, 0)
        if count >= max_per_author:
            continue
        counts[video.author] = count + 1
        selected.append(video)
    return selected


def _record_failure(sink: DownloadSink, video: Video, reason: str) -> None:
    try:
        sink.save_failure(video, reason)
    except SupplierError as ex:
        logger.bind(error=ex, error_type=type(ex).__name__).error(
            "Не удалось сохранить ошибку скачивания {}", video.video_id
        )


def _download_worker(
    video: Video,
    cfg: SupplierConfig,
    proxy: str,
    sink: DownloadSink,
) -> tuple[str, DownloadedVideo | None]:
    try:
        staged, sha256, size = _fetch_to_tmp(video, cfg)
    except SupplierError as ex:
        reason = _sanitize(str(ex), proxy)
        _record_failure(sink, video, reason)
        logger.bind(error=ex, error_type=type(ex).__name__).error(
            "Не удалось скачать {}: {}", video.video_id, reason
        )
        return "failed", None

    final_path = cfg.content_dir / f"{sha256}.mp4"
    with _FINALIZE_LOCK:
        moved = False
        try:
            if sink.has_sha256(sha256):
                staged.unlink(missing_ok=True)
                sink.save_rejection(video, "duplicate_sha256")
                logger.bind(video_id=video.video_id, sha256=sha256).warning(
                    "Видео {} отклонено: duplicate_sha256", video.video_id
                )
                return "rejected", None

            staged.replace(final_path)
            moved = True
            item = DownloadedVideo(
                video=video,
                path=str(final_path),
                sha256=sha256,
                size=size,
            )
            sink.save_download(item)
        except (SupplierError, OSError) as ex:
            staged.unlink(missing_ok=True)
            if moved:
                final_path.unlink(missing_ok=True)
            reason = _clean_error(ex, proxy)
            _record_failure(sink, video, reason)
            logger.bind(error=ex, error_type=type(ex).__name__).error(
                "Не удалось сохранить скачивание {}: {}", video.video_id, reason
            )
            return "failed", None

    logger.bind(video_id=video.video_id, path=str(item.path)).info(
        "Скачано: {} -> {}", video.video_id, item.path
    )
    return "downloaded", item


def _throttled_worker(
    video: Video,
    cfg: SupplierConfig,
    proxy: str,
    sink: DownloadSink,
) -> tuple[str, DownloadedVideo | None]:
    outcome = _download_worker(video, cfg, proxy, sink)
    if cfg.download_pause > 0:
        time.sleep(cfg.download_pause)
    return outcome


def download_videos(
    cfg: SupplierConfig,
    videos: Sequence[Video],
    sink: DownloadSink,
    limit: int | None = None,
) -> DownloadResult:
    """Скачать одобренные видео, по одному на автора.

    Args:
        cfg: Настройки с путями, числом потоков и паузой.
        videos: Одобренные видео.
        sink: Получатель скачиваний и ошибок.
        limit: Максимум успешных скачиваний; ``None`` означает без предела.

    Returns:
        Скачанные видео и счётчики отказов и сбоев.
    """
    selected = _select_videos(videos, cfg.max_videos_per_author)
    if not selected:
        return DownloadResult()

    maximum = len(selected) if limit is None else max(0, limit)
    if maximum == 0:
        return DownloadResult()

    proxy = resolve_proxy(cfg)
    download_cfg = cfg if cfg.proxy == proxy else replace(cfg, proxy=proxy)
    downloaded: list[DownloadedVideo] = []
    failed = 0
    rejected = 0
    iterator = iter(selected)
    futures: dict[Future[tuple[str, DownloadedVideo | None]], Video] = {}

    with ThreadPoolExecutor(max_workers=cfg.download_workers) as pool:

        def submit_next() -> bool:
            video = next(iterator, None)
            if video is None:
                return False
            futures[pool.submit(_throttled_worker, video, download_cfg, proxy, sink)] = video
            return True

        while len(futures) < cfg.download_workers and len(downloaded) + len(futures) < maximum:
            if not submit_next():
                break

        while futures:
            done, _ = wait(futures, return_when=FIRST_COMPLETED)
            for future in done:
                futures.pop(future)
                try:
                    outcome, item = future.result()
                except Exception as ex:  # pragma: no cover - defensive
                    failed += 1
                    logger.bind(error=ex, error_type=type(ex).__name__).error(
                        "Неожиданная ошибка скачивания"
                    )
                    continue
                if outcome == "downloaded" and item is not None:
                    downloaded.append(item)
                elif outcome == "rejected":
                    rejected += 1
                else:
                    failed += 1

            while len(futures) < cfg.download_workers and len(downloaded) + len(futures) < maximum:
                if not submit_next():
                    break

    return DownloadResult(
        downloaded=tuple(downloaded),
        failed=failed,
        rejected=rejected,
    )


class Downloader:
    """Этап скачивания одобренных видео одного языка."""

    def __init__(self, cfg: SupplierConfig, repo: Repository) -> None:
        """Создать этап скачивания.

        Args:
            cfg: Настройки поставщика.
            repo: Репозиторий одобренных и скачанных видео.
        """
        self._cfg = cfg
        self._repo = repo

    def download(self, limit: int) -> DownloadResult:
        """Скачать не более ``limit`` видео.

        Одобренные видео, чей автор уже отдал файл, отклоняются до запуска
        загрузчика.

        Args:
            limit: Максимум успешных скачиваний.

        Returns:
            Скачанные видео и счётчики отказов и сбоев.
        """
        used = self._repo.used_authors()
        rejected = 0
        for video in self._repo.list_videos(STATUS_APPROVED, language=self._cfg.language):
            if video.author in used:
                self._repo.save_rejection(video, "author_used")
                rejected += 1

        approved = self._repo.list_videos(STATUS_APPROVED, language=self._cfg.language)
        if not approved or limit <= 0:
            return DownloadResult(rejected=rejected)

        result = download_videos(self._cfg, approved, self._repo, limit=limit)
        return replace(result, rejected=result.rejected + rejected)
