from __future__ import annotations

import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import delete, select

from server_tg_home.core.config import Settings
from server_tg_home.database.models import AppState, SensorReading, Video, utcnow
from server_tg_home.database.session import new_session
from server_tg_home.media.camera_health import CameraHealthStatus, evaluate_camera_health
from server_tg_home.media.storage import ensure_storage, folder_size_bytes, format_bytes, iter_video_files
from server_tg_home.telegram.client import TelegramClient

logger = logging.getLogger(__name__)

CAMERA_HEALTH_STATE_PREFIX = "camera_health:"


class RetentionWorker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.telegram = TelegramClient(settings.telegram) if settings.telegram.bot_token else None
        self.last_warning_at = 0.0
        self.started_at = time.monotonic()

    def run_forever(self, once: bool = False) -> None:
        ensure_storage(self.settings)
        if once:
            self.check_once()
            self.check_camera_health()
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
        if self.settings.camera_health.enabled:
            scheduler.add_job(
                self.check_camera_health,
                trigger="interval",
                seconds=self.settings.camera_health.poll_sec,
                id="camera_health",
                max_instances=1,
                coalesce=True,
                next_run_time=None,
            )
        self.check_once()
        self.check_camera_health()
        scheduler.start()

    def check_once(self) -> None:
        self._cleanup_sensor_history()
        self._cleanup_graph_artifacts()

        size = folder_size_bytes(self.settings.storage.path)
        max_size = self.settings.storage.max_size_bytes
        if max_size <= 0:
            return
        percent = size / max_size * 100

        if size >= self.settings.storage.max_size_bytes:
            self._cleanup(size)
            return

        if size >= self.settings.storage.warning_threshold_bytes:
            now = time.monotonic()
            if now - self.last_warning_at >= self.settings.storage.warning_cooldown_sec:
                self.last_warning_at = now
                self._notify(
                    "Storage warning\n"
                    f"Used: {format_bytes(size)} / {format_bytes(max_size)} ({percent:.1f}%).\n"
                    f"If the limit is reached, up to {self.settings.storage.delete_batch_size} "
                    "oldest videos will be deleted."
                )

    def check_camera_health(self) -> None:
        if not self.settings.camera_health.enabled:
            return
        notifications: list[str] = []
        session = new_session()
        try:
            for status in evaluate_camera_health(self.settings):
                if not status.notifiable:
                    continue
                previous_state = self._previous_camera_health_state(session, status.camera_id)
                if self._inside_startup_grace() and previous_state is None and status.state == "unavailable":
                    continue
                self._save_camera_health_state(session, status)
                if status.state == "unavailable" and previous_state != "unavailable":
                    notifications.append(self._camera_unavailable_text(status))
                elif (
                    status.state == "ok"
                    and previous_state == "unavailable"
                    and self.settings.camera_health.notify_recovery
                ):
                    notifications.append(self._camera_recovered_text(status))
            session.commit()
        finally:
            session.close()

        for notification in notifications:
            self._notify_camera_health(notification)

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

    def _cleanup_sensor_history(self) -> None:
        retention_days = self.settings.graphs.history_retention_days
        if retention_days <= 0:
            return
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        session = new_session()
        try:
            session.execute(delete(SensorReading).where(SensorReading.received_at < cutoff))
            session.commit()
        finally:
            session.close()

    def _cleanup_graph_artifacts(self) -> None:
        retention_days = self.settings.graphs.artifact_retention_days
        if retention_days <= 0 or not self.settings.graphs.path.exists():
            return
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        for path in self.settings.graphs.path.rglob("*"):
            if not path.is_file():
                continue
            try:
                if datetime.fromtimestamp(path.stat().st_mtime, UTC) < cutoff:
                    path.unlink(missing_ok=True)
            except FileNotFoundError:
                continue

    def _inside_startup_grace(self) -> bool:
        grace_sec = self.settings.camera_health.startup_grace_sec
        return grace_sec > 0 and time.monotonic() - self.started_at < grace_sec

    def _previous_camera_health_state(self, session, camera_id: str) -> str | None:
        row = session.get(AppState, f"{CAMERA_HEALTH_STATE_PREFIX}{camera_id}")
        if row is None:
            return None
        state = row.value.get("state")
        return str(state) if state is not None else None

    def _save_camera_health_state(self, session, status: CameraHealthStatus) -> None:
        key = f"{CAMERA_HEALTH_STATE_PREFIX}{status.camera_id}"
        value = {
            "state": status.state,
            "reason": status.reason,
            "last_segment_at": status.last_segment_at.isoformat() if status.last_segment_at else None,
            "last_segment_age_sec": status.last_segment_age_sec,
            "checked_at": utcnow().isoformat(),
        }
        row = session.get(AppState, key)
        if row is None:
            session.add(AppState(key=key, value=value, updated_at=utcnow()))
            return
        row.value = value
        row.updated_at = utcnow()

    def _camera_unavailable_text(self, status: CameraHealthStatus) -> str:
        lines = [
            "Camera unavailable",
            f"Camera: {status.camera_id}",
            f"Reason: {status.reason}",
        ]
        if status.last_segment_age_sec is not None:
            lines.append(f"Last segment: {_format_seconds(status.last_segment_age_sec)} ago")
        return "\n".join(lines)

    def _camera_recovered_text(self, status: CameraHealthStatus) -> str:
        lines = [
            "Camera recovered",
            f"Camera: {status.camera_id}",
            f"Reason: {status.reason}",
        ]
        if status.last_segment_age_sec is not None:
            lines.append(f"Last segment: {_format_seconds(status.last_segment_age_sec)} ago")
        return "\n".join(lines)

    def _notify(self, text: str) -> None:
        chat_ids = self.settings.storage.notify_chat_ids or self.settings.telegram.default_chat_ids
        message_thread_id = (
            self.settings.storage.notify_message_thread_id
            if self.settings.storage.notify_message_thread_id is not None
            else self.settings.telegram.default_message_thread_id
        )
        if self.telegram is None or not chat_ids:
            logger.info(text.replace("\n", " | "))
            return
        for chat_id in chat_ids:
            try:
                self.telegram.send_message(chat_id, text, message_thread_id=message_thread_id)
            except Exception:
                logger.exception("Failed to send retention notification to chat %s", chat_id)

    def _notify_camera_health(self, text: str) -> None:
        chat_ids = self.settings.camera_health.notify_chat_ids or self.settings.telegram.default_chat_ids
        message_thread_id = (
            self.settings.camera_health.notify_message_thread_id
            if self.settings.camera_health.notify_message_thread_id is not None
            else self.settings.telegram.default_message_thread_id
        )
        if self.telegram is None or not chat_ids:
            logger.info(text.replace("\n", " | "))
            return
        for chat_id in chat_ids:
            try:
                self.telegram.send_message(chat_id, text, message_thread_id=message_thread_id)
            except Exception:
                logger.exception("Failed to send camera health notification to chat %s", chat_id)


def _format_seconds(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"
