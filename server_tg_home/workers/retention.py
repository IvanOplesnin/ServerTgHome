from __future__ import annotations

import logging
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import select

from server_tg_home.core.config import Settings
from server_tg_home.database.models import Video, utcnow
from server_tg_home.database.session import new_session
from server_tg_home.media.storage import ensure_storage, folder_size_bytes, format_bytes, iter_video_files
from server_tg_home.telegram.client import TelegramClient

logger = logging.getLogger(__name__)


class RetentionWorker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.telegram = TelegramClient(settings.telegram) if settings.telegram.bot_token else None
        self.last_warning_at = 0.0

    def run_forever(self, once: bool = False) -> None:
        ensure_storage(self.settings)
        if once:
            self.check_once()
            return

        logger.info("Retention scheduler started")
        scheduler = BlockingScheduler(timezone="UTC")
        scheduler.add_job(
            self.check_once,
            trigger="interval",
            seconds=self.settings.storage.retention_poll_sec,
            id="storage_retention",
            max_instances=1,
            coalesce=True,
            next_run_time=None,
        )
        self.check_once()
        scheduler.start()

    def check_once(self) -> None:
        size = folder_size_bytes(self.settings.storage.path)
        max_size = self.settings.storage.max_size_bytes
        if max_size <= 0:
            return
        percent = size / max_size * 100

        if size >= self.settings.storage.max_size_bytes:
            self._cleanup(size)
            return

        if size >= self.settings.storage.warning_threshold_bytes:
            import time

            now = time.monotonic()
            if now - self.last_warning_at >= self.settings.storage.warning_cooldown_sec:
                self.last_warning_at = now
                self._notify(
                    "Storage warning\n"
                    f"Used: {format_bytes(size)} / {format_bytes(max_size)} ({percent:.1f}%).\n"
                    f"If the limit is reached, up to {self.settings.storage.delete_batch_size} "
                    "oldest videos will be deleted."
                )

    def _cleanup(self, current_size: int) -> None:
        target = self.settings.storage.cleanup_target_bytes
        size = current_size
        deleted: list[Path] = []
        for video in iter_video_files(self.settings.storage.path):
            if size <= target or len(deleted) >= self.settings.storage.delete_batch_size:
                break
            try:
                file_size = video.stat().st_size
                video.unlink()
            except FileNotFoundError:
                continue
            deleted.append(video)
            size -= file_size

        if deleted:
            self._mark_deleted(deleted)
            self._notify(
                "Storage cleanup completed\n"
                f"Deleted videos: {len(deleted)}\n"
                f"Before: {format_bytes(current_size)}\n"
                f"After: {format_bytes(max(size, 0))}"
            )
            logger.warning("Deleted %s videos during retention cleanup", len(deleted))
        else:
            self._notify(
                "Storage cleanup could not free space: no deletable videos were found."
            )

    def _mark_deleted(self, paths: list[Path]) -> None:
        path_values = {str(path) for path in paths}
        session = new_session()
        try:
            rows = session.execute(select(Video).where(Video.path.in_(path_values))).scalars().all()
            for video in rows:
                video.deleted_at = utcnow()
            session.commit()
        finally:
            session.close()

    def _notify(self, text: str) -> None:
        chat_ids = self.settings.storage.notify_chat_ids or self.settings.telegram.default_chat_ids
        if self.telegram is None or not chat_ids:
            logger.info(text.replace("\n", " | "))
            return
        for chat_id in chat_ids:
            try:
                self.telegram.send_message(chat_id, text)
            except Exception:
                logger.exception("Failed to send retention notification to chat %s", chat_id)
