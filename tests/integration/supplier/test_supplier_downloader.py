from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

import pytest
from loguru import logger
from tests.unit.supplier.supplier_helpers import make_config, make_metadata, make_video

from content_factory.exceptions.errors import ConfigError, SupplierError
from content_factory.schemas.supplier.results import DownloadResult, FilterDecision
from content_factory.schemas.supplier.settings import SupplierConfig
from content_factory.schemas.supplier.video import (
    STATUS_APPROVED,
    DownloadedVideo,
    Video,
)
from content_factory.services.supplier import downloader
from content_factory.services.supplier.downloader import (
    Downloader,
    _clean_error,
    _fetch_to_tmp,
    _match_filter,
    _record_failure,
    _sanitize,
    _throttled_worker,
    _YtDlpLogger,
    download_videos,
)
from content_factory.services.supplier.repository import Repository


class FakeSink:
    def __init__(
        self,
        existing_sha: set[str] | None = None,
        download_error: SupplierError | None = None,
    ) -> None:
        self.downloads: list[DownloadedVideo] = []
        self.failures: list[tuple[Video, str]] = []
        self.rejections: list[tuple[Video, str]] = []
        self._existing_sha = set(existing_sha or ())
        self._download_error = download_error

    def has_sha256(self, sha256: str) -> bool:
        return sha256 in self._existing_sha

    def save_download(self, item: DownloadedVideo) -> None:
        if self._download_error is not None:
            raise self._download_error
        self.downloads.append(item)

    def save_failure(self, video: Video, reason: str) -> None:
        self.failures.append((video, reason))

    def save_rejection(self, video: Video, reason: str) -> None:
        self.rejections.append((video, reason))


def _patch_ydl(
    monkeypatch: pytest.MonkeyPatch,
    info: dict[str, object] | None,
    payload: bytes = b"video-bytes",
) -> None:
    class FakeYDL:
        def __init__(self, opts: dict[str, object]) -> None:
            self.opts = opts

        def __enter__(self) -> FakeYDL:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def extract_info(self, url: str, download: bool = True) -> dict[str, object] | None:
            del url, download
            Path(str(self.opts["outtmpl"])).write_bytes(payload)
            return info

    monkeypatch.setattr(downloader, "YoutubeDL", FakeYDL)


def test_fetch_to_tmp_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_ydl(monkeypatch, {"ext": "mp4", "vcodec": "h264"})
    cfg = make_config(tmp_path)
    video = make_video()

    staged, sha256, size = _fetch_to_tmp(video, cfg)

    assert staged == cfg.content_dir / f"{sha256}.tmp"
    assert staged.read_bytes() == b"video-bytes"
    assert sha256 == hashlib.sha256(b"video-bytes").hexdigest()
    assert size == len(b"video-bytes")
    assert list((cfg.content_dir / ".tmp").iterdir()) == []


def test_fetch_to_tmp_wraps_directory_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("denied")

    monkeypatch.setattr(Path, "mkdir", fail)

    with pytest.raises(SupplierError, match="denied"):
        _fetch_to_tmp(make_video(), make_config(tmp_path))


def test_fetch_to_tmp_replaces_stale_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ydl(monkeypatch, {"ext": "mp4", "vcodec": "h264"})
    cfg = make_config(tmp_path)
    tmp_dir = cfg.content_dir / ".tmp"
    tmp_dir.mkdir(parents=True)
    (tmp_dir / "111.part").write_bytes(b"stale")

    staged, _, _ = _fetch_to_tmp(make_video(), cfg)

    assert staged.read_bytes() == b"video-bytes"


def test_fetch_to_tmp_rejects_audio(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_ydl(monkeypatch, {"ext": "m4a", "vcodec": "none"})

    with pytest.raises(SupplierError, match="фото-пост или аудио"):
        _fetch_to_tmp(make_video(), make_config(tmp_path))


def test_fetch_to_tmp_rejects_non_mp4(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_ydl(monkeypatch, {"ext": "webm", "vcodec": "vp9"})

    with pytest.raises(SupplierError, match="нужен mp4"):
        _fetch_to_tmp(make_video(), make_config(tmp_path))


def test_fetch_to_tmp_rejects_playlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_ydl(monkeypatch, {"_type": "playlist"})

    with pytest.raises(SupplierError, match="плейлист"):
        _fetch_to_tmp(make_video(), make_config(tmp_path))


def test_fetch_to_tmp_rejects_missing_info(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_ydl(monkeypatch, None)

    with pytest.raises(SupplierError, match="не вернул информацию"):
        _fetch_to_tmp(make_video(), make_config(tmp_path))


def test_fetch_to_tmp_rejects_missing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeYDL:
        def __init__(self, opts: dict[str, object]) -> None:
            self.opts = opts

        def __enter__(self) -> FakeYDL:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def extract_info(self, url: str, download: bool = True) -> dict[str, object]:
            del url, download
            return {"ext": "mp4", "vcodec": "h264"}

    monkeypatch.setattr(downloader, "YoutubeDL", FakeYDL)

    with pytest.raises(SupplierError, match="не создал видеофайл"):
        _fetch_to_tmp(make_video(), make_config(tmp_path))


def test_fetch_to_tmp_sanitizes_proxy_in_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proxy = "http://user:secret@127.0.0.1:8080"

    class FakeYDL:
        def __init__(self, opts: dict[str, object]) -> None:
            self.opts = opts

        def __enter__(self) -> FakeYDL:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def extract_info(self, url: str, download: bool = True) -> dict[str, object]:
            del url, download
            raise RuntimeError(f"network error via {proxy}")

    monkeypatch.setattr(downloader, "YoutubeDL", FakeYDL)

    with pytest.raises(SupplierError) as error:
        _fetch_to_tmp(make_video(), make_config(tmp_path, proxy=proxy))
    assert "secret" not in str(error.value)
    assert "<proxy>" in str(error.value)


def test_download_videos_finalizes_sha_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_config(tmp_path)
    video = make_video()

    def fake_fetch(item: Video, config: SupplierConfig) -> tuple[Path, str, int]:
        del item
        config.content_dir.mkdir(parents=True, exist_ok=True)
        staged = config.content_dir / "abc.tmp"
        staged.write_bytes(b"video")
        return staged, "abc", 5

    monkeypatch.setattr(downloader, "_fetch_to_tmp", fake_fetch)
    sink = FakeSink()

    result = download_videos(cfg, [video], sink)

    assert [item.video.video_id for item in result.downloaded] == ["111"]
    assert result.failed == 0
    assert result.rejected == 0
    assert (cfg.content_dir / "abc.mp4").read_bytes() == b"video"
    assert not (cfg.content_dir / "abc.tmp").exists()
    assert sink.downloads == list(result.downloaded)


def test_download_videos_downloads_one_per_author(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_config(tmp_path)
    videos = [make_video("1", "alice"), make_video("2", "alice"), make_video("3", "bob")]
    calls: list[str] = []

    def fake_worker(
        video: Video,
        config: SupplierConfig,
        proxy: str,
        sink: FakeSink,
    ) -> tuple[str, DownloadedVideo | None]:
        del config, proxy, sink
        calls.append(video.video_id)
        item = DownloadedVideo(
            video=video,
            path=f"videos/{video.video_id}.mp4",
            sha256=video.video_id,
            size=1,
        )
        return "downloaded", item

    monkeypatch.setattr(downloader, "_download_worker", fake_worker)

    result = download_videos(cfg, videos, FakeSink())

    assert calls == ["1", "3"]
    assert [item.video.video_id for item in result.downloaded] == ["1", "3"]


def test_download_videos_rejects_duplicate_sha256(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_config(tmp_path)
    video = make_video()

    def fake_fetch(_video: Video, config: SupplierConfig) -> tuple[Path, str, int]:
        config.content_dir.mkdir(parents=True, exist_ok=True)
        staged = config.content_dir / "same.tmp"
        staged.write_bytes(b"same")
        return staged, "same", 4

    monkeypatch.setattr(downloader, "_fetch_to_tmp", fake_fetch)
    sink = FakeSink(existing_sha={"same"})

    result = download_videos(cfg, [video], sink)

    assert result.downloaded == ()
    assert result.rejected == 1
    assert result.failed == 0
    assert sink.rejections == [(video, "duplicate_sha256")]
    assert not (cfg.content_dir / "same.mp4").exists()
    assert not (cfg.content_dir / "same.tmp").exists()


def test_download_videos_records_download_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_config(tmp_path, proxy="http://user:secret@127.0.0.1:8080")
    video = make_video()

    def fake_fetch(item: Video, config: SupplierConfig) -> tuple[Path, str, int]:
        del item
        raise SupplierError(f"network down via {config.proxy}")

    monkeypatch.setattr(downloader, "_fetch_to_tmp", fake_fetch)
    sink = FakeSink()

    result = download_videos(cfg, [video], sink)

    assert result.downloaded == ()
    assert result.failed == 1
    assert len(sink.failures) == 1
    reason = sink.failures[0][1]
    assert "secret" not in reason
    assert "<proxy>" in reason


def test_download_videos_removes_file_when_save_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_config(tmp_path)
    video = make_video()

    def fake_fetch(_video: Video, config: SupplierConfig) -> tuple[Path, str, int]:
        config.content_dir.mkdir(parents=True, exist_ok=True)
        staged = config.content_dir / "unique.tmp"
        staged.write_bytes(b"video")
        return staged, "unique", 5

    monkeypatch.setattr(downloader, "_fetch_to_tmp", fake_fetch)
    sink = FakeSink(download_error=SupplierError("db is locked"))

    result = download_videos(cfg, [video], sink)

    assert result.failed == 1
    assert result.downloaded == ()
    assert sink.failures == [(video, "db is locked")]
    assert not (cfg.content_dir / "unique.mp4").exists()
    assert not (cfg.content_dir / "unique.tmp").exists()


def test_download_videos_reports_unusable_lock_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    cfg = make_config(tmp_path, db_path=blocker / "db" / "db.sqlite3")
    video = make_video()

    def fake_fetch(_video: Video, config: SupplierConfig) -> tuple[Path, str, int]:
        config.content_dir.mkdir(parents=True, exist_ok=True)
        staged = config.content_dir / "locked.tmp"
        staged.write_bytes(b"video")
        return staged, "locked", 5

    monkeypatch.setattr(downloader, "_fetch_to_tmp", fake_fetch)
    sink = FakeSink()

    result = download_videos(cfg, [video], sink)

    assert result.failed == 1
    assert result.downloaded == ()
    assert len(sink.failures) == 1
    assert "каталог блокировок" in sink.failures[0][1]
    assert not (cfg.content_dir / "locked.tmp").exists()
    assert not (cfg.content_dir / "locked.mp4").exists()


def test_download_videos_keeps_committed_file_when_save_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_config(tmp_path)
    video = make_video()

    def fake_fetch(_video: Video, config: SupplierConfig) -> tuple[Path, str, int]:
        config.content_dir.mkdir(parents=True, exist_ok=True)
        staged = config.content_dir / "shared.tmp"
        staged.write_bytes(b"video")
        return staged, "shared", 5

    class RacingSink(FakeSink):
        def __init__(self) -> None:
            super().__init__()
            self._checks = 0

        def has_sha256(self, sha256: str) -> bool:
            del sha256
            self._checks += 1
            return self._checks > 1

        def save_download(self, item: DownloadedVideo) -> None:
            del item
            raise SupplierError("UNIQUE constraint failed: downloads.sha256")

    monkeypatch.setattr(downloader, "_fetch_to_tmp", fake_fetch)
    sink = RacingSink()

    result = download_videos(cfg, [video], sink)

    assert result.failed == 1
    assert (cfg.content_dir / "shared.mp4").exists()


def test_download_videos_keeps_file_when_commit_check_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_config(tmp_path)
    video = make_video()

    def fake_fetch(_video: Video, config: SupplierConfig) -> tuple[Path, str, int]:
        config.content_dir.mkdir(parents=True, exist_ok=True)
        staged = config.content_dir / "shared.tmp"
        staged.write_bytes(b"video")
        return staged, "shared", 5

    class BrokenCheckSink(FakeSink):
        def __init__(self) -> None:
            super().__init__()
            self._checks = 0

        def has_sha256(self, sha256: str) -> bool:
            del sha256
            self._checks += 1
            if self._checks == 1:
                return False
            raise SupplierError("db is down")

        def save_download(self, item: DownloadedVideo) -> None:
            del item
            raise SupplierError("db is down")

    monkeypatch.setattr(downloader, "_fetch_to_tmp", fake_fetch)
    sink = BrokenCheckSink()

    result = download_videos(cfg, [video], sink)

    assert result.failed == 1
    assert (cfg.content_dir / "shared.mp4").exists()


def test_downloader_continues_when_author_rejection_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_config(tmp_path)
    repo = Repository(cfg.db_path)
    repo.init_schema()
    used = make_video("used", "bob")
    assert repo.add_found(used)
    repo.save_download(
        DownloadedVideo(video=used, path="downloads/used.mp4", sha256="sha-used", size=1)
    )
    stale = make_video("stale", "bob")
    fresh = make_video("fresh", "alice")
    for video in (stale, fresh):
        assert repo.add_found(video)
        repo.save_decision(video, make_metadata(video), FilterDecision(True))

    def fail(*args: object, **kwargs: object) -> None:
        raise SupplierError("db is down")

    monkeypatch.setattr(repo, "save_rejection", fail)
    calls: list[tuple[list[str], int | None]] = []

    def fake_download_videos(
        config: SupplierConfig,
        videos: Sequence[Video],
        sink: object,
        limit: int | None = None,
    ) -> DownloadResult:
        del config, sink
        calls.append(([video.video_id for video in videos], limit))
        return DownloadResult()

    monkeypatch.setattr(downloader, "download_videos", fake_download_videos)

    result = Downloader(cfg, repo).download(limit=2)

    assert calls == [(["fresh"], 2)]
    assert result.rejected == 1


def test_download_videos_zero_limit_does_not_touch_proxy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        downloader,
        "resolve_proxy",
        lambda cfg: pytest.fail("proxy must not be resolved"),
    )

    result = download_videos(make_config(tmp_path), [make_video()], FakeSink(), limit=0)

    assert result.downloaded == ()
    assert result.failed == 0
    assert result.rejected == 0


def test_download_videos_empty_input_returns_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        downloader,
        "resolve_proxy",
        lambda cfg: pytest.fail("proxy must not be resolved"),
    )

    result = download_videos(make_config(tmp_path), [], FakeSink())

    assert result.downloaded == ()


def test_download_videos_honours_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_config(tmp_path)
    videos = [make_video("1", "alice"), make_video("2", "bob"), make_video("3", "carol")]
    calls: list[str] = []

    def fake_worker(
        video: Video,
        config: SupplierConfig,
        proxy: str,
        sink: FakeSink,
    ) -> tuple[str, DownloadedVideo | None]:
        del config, proxy, sink
        calls.append(video.video_id)
        item = DownloadedVideo(video=video, path="x.mp4", sha256=video.video_id, size=1)
        return "downloaded", item

    monkeypatch.setattr(downloader, "_download_worker", fake_worker)

    result = download_videos(cfg, videos, FakeSink(), limit=2)

    assert len(calls) == 2
    assert len(result.downloaded) == 2


def test_download_videos_propagates_proxy_config_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(cfg: SupplierConfig) -> str:
        raise ConfigError("no proxy")

    monkeypatch.setattr(downloader, "resolve_proxy", fail)

    with pytest.raises(ConfigError, match="no proxy"):
        download_videos(make_config(tmp_path), [make_video()], FakeSink())


def test_downloader_rejects_used_authors_and_scopes_language(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_config(tmp_path)
    repo = Repository(cfg.db_path)
    repo.init_schema()
    used = make_video("used", "bob")
    assert repo.add_found(used)
    repo.save_download(
        DownloadedVideo(video=used, path="downloads/used.mp4", sha256="sha-used", size=1)
    )
    stale = make_video("stale", "bob")
    fresh = make_video("fresh", "alice")
    foreign = make_video("foreign", "carol", language="en")
    for video in (stale, fresh, foreign):
        assert repo.add_found(video)
        repo.save_decision(video, make_metadata(video), FilterDecision(True))

    calls: list[tuple[list[Video], int | None]] = []

    def fake_download_videos(
        config: SupplierConfig,
        videos: Sequence[Video],
        sink: object,
        limit: int | None = None,
    ) -> DownloadResult:
        del config, sink
        calls.append((list(videos), limit))
        return DownloadResult(rejected=1)

    monkeypatch.setattr(downloader, "download_videos", fake_download_videos)

    result = Downloader(cfg, repo).download(limit=2)

    assert calls == [([fresh], 2)]
    assert result.rejected == 2
    assert repo.list_videos(STATUS_APPROVED, language="ru") == [fresh]
    assert repo.list_videos(STATUS_APPROVED, language="en") == [foreign]


def test_downloader_returns_when_no_approved(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    repo = Repository(cfg.db_path)
    repo.init_schema()

    result = Downloader(cfg, repo).download(limit=2)

    assert result == DownloadResult()


def test_ytdlp_logger_bridges_and_strips_debug_prefix() -> None:
    messages: list[str] = []
    handler = logger.add(lambda message: messages.append(message.record["message"]), level="DEBUG")
    try:
        ydl_logger = _YtDlpLogger()
        ydl_logger.debug("[debug] detail")
        ydl_logger.debug("plain")
        ydl_logger.info("info")
        ydl_logger.warning("warning")
        ydl_logger.error("error")
    finally:
        logger.remove(handler)

    assert "detail" in messages
    assert "plain" in messages
    assert "info" in messages
    assert "warning" in messages
    assert "error" in messages


def test_ytdlp_logger_hides_proxy_on_every_level() -> None:
    proxy = "http://user:secret@127.0.0.1:8080"
    messages: list[str] = []
    handler = logger.add(lambda message: messages.append(message.record["message"]), level="DEBUG")
    try:
        ydl_logger = _YtDlpLogger(proxy)
        for emit in (ydl_logger.debug, ydl_logger.info, ydl_logger.warning, ydl_logger.error):
            emit(f"failed via {proxy}")
    finally:
        logger.remove(handler)

    assert len(messages) == 4
    assert all("secret" not in message for message in messages)
    assert all("<proxy>" in message for message in messages)


def test_sanitize_handles_missing_proxy_and_invalid_url() -> None:
    assert _sanitize("boom", "") == "boom"
    assert _sanitize("boom", "http://[") == "boom"


def test_sanitize_hides_username_without_password() -> None:
    proxy = "http://user@127.0.0.1:8080"
    result = _sanitize(f"failed via {proxy}", proxy)

    assert "user" not in result
    assert "<proxy>" in result


def test_clean_error_strips_prefix_and_uses_class_name() -> None:
    assert _clean_error(ValueError("ERROR: bad"), "") == "bad"
    assert _clean_error(ValueError(""), "") == "ValueError"


def test_match_filter_rejects_playlists_and_photo_posts() -> None:
    assert _match_filter({"_type": "playlist"}) is not None
    assert _match_filter({"vcodec": "none"}) is not None
    assert _match_filter({"vcodec": "h264"}) is None


def test_record_failure_swallows_sink_error() -> None:
    video = make_video()

    class FailingSink(FakeSink):
        def save_failure(self, video: Video, reason: str) -> None:
            raise SupplierError("db is down")

    _record_failure(FailingSink(), video, "metadata_error")


def test_throttled_worker_sleeps_after_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(downloader.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        downloader,
        "_download_worker",
        lambda video, cfg, proxy, sink: ("failed", None),
    )

    outcome = _throttled_worker(
        make_video(),
        make_config(tmp_path, download_pause=0.5),
        "http://proxy",
        FakeSink(),
    )

    assert outcome == ("failed", None)
    assert sleeps == [0.5]


def test_download_videos_stops_submitting_when_input_runs_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_config(tmp_path, download_workers=2)
    video = make_video()

    def fake_worker(
        item: Video,
        config: SupplierConfig,
        proxy: str,
        sink: FakeSink,
    ) -> tuple[str, DownloadedVideo | None]:
        del config, proxy, sink
        return "downloaded", DownloadedVideo(video=item, path="x.mp4", sha256="x", size=1)

    monkeypatch.setattr(downloader, "_download_worker", fake_worker)

    result = download_videos(cfg, [video], FakeSink(), limit=5)

    assert [item.video.video_id for item in result.downloaded] == ["111"]


def test_download_videos_removes_staged_file_when_rename_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_config(tmp_path)
    video = make_video()

    def fake_fetch(_video: Video, config: SupplierConfig) -> tuple[Path, str, int]:
        config.content_dir.mkdir(parents=True, exist_ok=True)
        staged = config.content_dir / "staged.tmp"
        staged.write_bytes(b"video")
        return staged, "sha", 5

    real_replace = Path.replace

    def fail_replace(self: Path, target: Path) -> Path:
        if self.name == "staged.tmp":
            raise OSError("rename failed")
        return real_replace(self, target)

    monkeypatch.setattr(downloader, "_fetch_to_tmp", fake_fetch)
    monkeypatch.setattr(Path, "replace", fail_replace)
    sink = FakeSink()

    result = download_videos(cfg, [video], sink)

    assert result.downloaded == ()
    assert result.failed == 1
    assert sink.failures == [(video, "rename failed")]
    assert not (cfg.content_dir / "staged.tmp").exists()
