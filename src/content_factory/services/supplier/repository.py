"""Repository of supplier videos and downloaded files."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from content_factory.db.connection import connect
from content_factory.db.errors import DatabaseError
from content_factory.exceptions.errors import SupplierError
from content_factory.schemas.database.port import Connection, Row
from content_factory.schemas.supplier.results import FilterDecision
from content_factory.schemas.supplier.video import (
    STATUS_APPROVED,
    STATUS_DOWNLOADED,
    STATUS_FAILED,
    STATUS_FOUND,
    STATUS_REJECTED,
    VIDEO_STATUSES,
    DownloadedVideo,
    Video,
    VideoMetadata,
    utcnow,
)


@contextmanager
def _connect(db_path: Path) -> Iterator[Connection]:
    """Open one write transaction through the database connection factory."""
    try:
        connection = connect(db_path)
    except OSError as ex:
        raise SupplierError(f"не удалось создать каталог {db_path.parent}: {ex}") from ex
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _row_to_video(row: Row) -> Video:
    """Build a video from one selected row."""
    return Video(
        video_id=str(row["video_id"]),
        url=str(row["url"]),
        author=str(row["author"]),
        query=str(row["query"]),
        language=str(row["language"]),
        discovered_at=str(row["discovered_at"]),
    )


class Repository:
    """Хранилище операционной базы, общее для этапов поставщика.

    Каждый метод открывает своё соединение, поэтому один репозиторий можно
    использовать из параллельных потоков фильтра и загрузчика.
    """

    def __init__(self, db_path: Path) -> None:
        """Создать репозиторий одного файла базы.

        Args:
            db_path: Путь к базе SQLite; в режиме PostgreSQL не используется.
        """
        self._db_path = db_path

    @property
    def db_path(self) -> Path:
        """Вернуть путь к базе."""
        return self._db_path

    def add_found(self, video: Video) -> bool:
        """Сохранить видео из поисковой выдачи.

        Args:
            video: Видео, найденное в ленте.

        Returns:
            True, если строка добавлена; False, если видео уже известно.

        Raises:
            SupplierError: Запись не удалась.
        """
        now = utcnow()
        try:
            with _connect(self._db_path) as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO videos (
                        video_id, language, url, author, query, status,
                        discovered_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT DO NOTHING
                    """,
                    (
                        video.video_id,
                        video.language,
                        video.url,
                        video.author,
                        video.query,
                        STATUS_FOUND,
                        video.discovered_at or now,
                        now,
                    ),
                )
                return cursor.rowcount > 0
        except DatabaseError as ex:
            raise SupplierError(f"не удалось сохранить видео {video.video_id}: {ex}") from ex

    def save_decision(
        self,
        video: Video,
        metadata: VideoMetadata,
        decision: FilterDecision,
    ) -> None:
        """Сохранить метаданные и решение фильтра.

        Args:
            video: Видео, чья строка обновляется.
            metadata: Метаданные страницы видео.
            decision: Результат контентных фильтров.

        Raises:
            SupplierError: Строка видео не найдена или запись не удалась.
        """
        status = STATUS_APPROVED if decision.approved else STATUS_REJECTED
        reason = None if decision.approved else decision.reason
        now = utcnow()
        try:
            with _connect(self._db_path) as conn:
                cursor = conn.execute(
                    """
                    UPDATE videos SET
                        url = ?,
                        views = ?,
                        followers = ?,
                        duration = ?,
                        width = ?,
                        height = ?,
                        create_time = ?,
                        description = ?,
                        is_aigc = ?,
                        status = ?,
                        reject_reason = ?,
                        updated_at = ?
                    WHERE video_id = ?
                    """,
                    (
                        metadata.url or video.url,
                        metadata.views,
                        metadata.followers,
                        metadata.duration,
                        metadata.width,
                        metadata.height,
                        metadata.create_time,
                        metadata.description,
                        int(metadata.has_aigc_signal()),
                        status,
                        reason,
                        now,
                        video.video_id,
                    ),
                )
                if cursor.rowcount == 0:
                    raise SupplierError(f"видео не найдено: {video.video_id}")
        except DatabaseError as ex:
            raise SupplierError(f"не удалось сохранить решение {video.video_id}: {ex}") from ex

    def save_rejection(self, video: Video, reason: str) -> None:
        """Пометить видео отклонённым.

        Args:
            video: Видео для обновления.
            reason: Машиночитаемая причина отказа.
        """
        self._set_status(video.video_id, STATUS_REJECTED, reason)

    def save_failure(self, video: Video, reason: str) -> None:
        """Пометить видео неудачным.

        Args:
            video: Видео для обновления.
            reason: Машиночитаемая причина сбоя.
        """
        self._set_status(video.video_id, STATUS_FAILED, reason)

    def save_download(self, item: DownloadedVideo) -> None:
        """Сохранить скачивание и отклонить другие видео того же автора.

        Запись скачивания, смена статуса видео и отказ остальных видео автора
        выполняются одной транзакцией: частичное состояние недопустимо.

        Args:
            item: Запись о скачанном видео.

        Raises:
            SupplierError: Скачивание или автор конфликтуют с базой.
        """
        now = utcnow()
        try:
            with _connect(self._db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO downloads (video_id, sha256, path, size, downloaded_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        item.video.video_id,
                        item.sha256,
                        item.path,
                        item.size,
                        now,
                    ),
                )
                conn.execute(
                    """
                    UPDATE videos
                    SET status = ?, reject_reason = NULL, updated_at = ?
                    WHERE video_id = ?
                    """,
                    (STATUS_DOWNLOADED, now, item.video.video_id),
                )
                conn.execute(
                    """
                    UPDATE videos
                    SET status = ?, reject_reason = ?, updated_at = ?
                    WHERE author = ? AND video_id <> ? AND status IN (?, ?)
                    """,
                    (
                        STATUS_REJECTED,
                        "author_used",
                        now,
                        item.video.author,
                        item.video.video_id,
                        STATUS_FOUND,
                        STATUS_APPROVED,
                    ),
                )
        except DatabaseError as ex:
            raise SupplierError(
                f"не удалось записать скачивание {item.video.video_id}: {ex}"
            ) from ex

    def has_sha256(self, sha256: str) -> bool:
        """Вернуть, есть ли уже файл с таким хэшем.

        Raises:
            SupplierError: Запрос к базе не удался.
        """
        try:
            with _connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT 1 FROM downloads WHERE sha256 = ?",
                    (sha256,),
                ).fetchone()
        except DatabaseError as ex:
            raise SupplierError(f"не удалось проверить SHA-256: {ex}") from ex
        return row is not None

    def decided_video_ids(self) -> set[str]:
        """Вернуть идентификаторы видео с финальным статусом.

        Raises:
            SupplierError: Запрос к базе не удался.
        """
        try:
            with _connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT video_id FROM videos WHERE status <> ?",
                    (STATUS_FOUND,),
                ).fetchall()
        except DatabaseError as ex:
            raise SupplierError(f"не удалось прочитать решения: {ex}") from ex
        return {str(row["video_id"]) for row in rows}

    def used_authors(self) -> set[str]:
        """Вернуть авторов, у которых уже есть скачанное видео.

        Raises:
            SupplierError: Запрос к базе не удался.
        """
        try:
            with _connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT DISTINCT author FROM videos WHERE status = ?",
                    (STATUS_DOWNLOADED,),
                ).fetchall()
        except DatabaseError as ex:
            raise SupplierError(f"не удалось прочитать авторов: {ex}") from ex
        return {str(row["author"]) for row in rows}

    def downloaded_count(self, language: str) -> int:
        """Посчитать скачанные видео одного языка.

        Raises:
            SupplierError: Запрос к базе не удался.
        """
        try:
            with _connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS count FROM videos WHERE language = ? AND status = ?",
                    (language, STATUS_DOWNLOADED),
                ).fetchone()
        except DatabaseError as ex:
            raise SupplierError(f"не удалось посчитать скачанные видео: {ex}") from ex
        return 0 if row is None else int(row["count"])

    def list_videos(
        self,
        status: str | None = None,
        *,
        language: str | None = None,
        limit: int | None = None,
    ) -> list[Video]:
        """Вернуть видео в порядке обнаружения.

        Args:
            status: Необязательный фильтр статуса.
            language: Необязательный фильтр языка.
            limit: Необязательный максимум строк.

        Returns:
            Видео в порядке обнаружения.

        Raises:
            ValueError: Статус не входит в список допустимых.
        """
        if status is not None and status not in VIDEO_STATUSES:
            raise ValueError(f"неизвестный статус видео: {status}")
        query = "SELECT video_id, url, author, query, language, discovered_at FROM videos"
        clauses: list[str] = []
        params: list[object] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if language is not None:
            clauses.append("language = ?")
            params.append(language)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY discovered_at, video_id"
        if limit is not None:
            query += " LIMIT ?"
            params.append(limit)
        try:
            with _connect(self._db_path) as conn:
                rows = conn.execute(query, params).fetchall()
        except DatabaseError as ex:
            raise SupplierError(f"не удалось прочитать видео: {ex}") from ex
        return [_row_to_video(row) for row in rows]

    def _set_status(self, video_id: str, status: str, reason: str | None) -> None:
        """Set one video status, raising when the row is missing."""
        try:
            with _connect(self._db_path) as conn:
                cursor = conn.execute(
                    """
                    UPDATE videos
                    SET status = ?, reject_reason = ?, updated_at = ?
                    WHERE video_id = ?
                    """,
                    (status, reason, utcnow(), video_id),
                )
                if cursor.rowcount == 0:
                    raise SupplierError(f"видео не найдено: {video_id}")
        except DatabaseError as ex:
            raise SupplierError(f"не удалось обновить видео {video_id}: {ex}") from ex


__all__ = ["Repository"]
