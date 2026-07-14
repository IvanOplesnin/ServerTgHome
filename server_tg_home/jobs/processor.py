from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from server_tg_home.core.config import Settings
from server_tg_home.database.models import Job, Video
from server_tg_home.database.session import new_session
from server_tg_home.integrations.home_assistant import HomeAssistantClient
from server_tg_home.jobs.repository import load_job, mark_done, mark_failed, mark_queued, mark_running
from server_tg_home.media.recorder import record_event_clip, record_snapshot
from server_tg_home.telegram.client import TelegramClient

logger = logging.getLogger(__name__)


class JobProcessor:
    def __init__(self, settings: Settings, retry_callback=None) -> None:
        self.settings = settings
        self.retry_callback = retry_callback
        self.telegram = TelegramClient(settings.telegram) if settings.telegram.bot_token else None
        self.ha = HomeAssistantClient(settings.home_assistant)

    def process_job_id(self, job_id: str) -> None:
        session = new_session()
        try:
            job = load_job(session, job_id)
            if job is None:
                logger.warning("Job %s does not exist", job_id)
                return
            if job.status == "done":
                return

            mark_running(job)
            session.commit()
            logger.info("Processing job %s type=%s attempt=%s", job.id, job.type, job.attempts)

            if job.type == "record_and_send_video":
                self._process_record_video(session, job)
            elif job.type == "snapshot_and_send":
                self._process_snapshot(job)
            elif job.type == "home_assistant_service":
                self._process_home_assistant(job)
            elif job.type == "send_message":
                self._process_send_message(job)
            else:
                raise ValueError(f"Unknown job type: {job.type}")

            mark_done(job)
            session.commit()
            logger.info("Job %s done", job.id)
        except Exception as exc:
            session.rollback()
            logger.exception("Job %s failed", job_id)
            self._fail_or_retry(job_id, str(exc))
        finally:
            session.close()

    def _fail_or_retry(self, job_id: str, error: str) -> None:
        session = new_session()
        try:
            job = load_job(session, job_id)
            if job is None:
                return
            if job.attempts < self.settings.app.max_job_attempts:
                mark_queued(job, error)
                session.commit()
                if self.retry_callback is not None:
                    self.retry_callback(job.id, delay_ms=2000)
                logger.info("Job %s requeued after failure", job.id)
            else:
                mark_failed(job, error)
                session.commit()
                logger.error("Job %s failed permanently: %s", job.id, error)
                self._notify_failure(job, error)
        finally:
            session.close()

    def _process_record_video(self, session: Session, job: Job) -> None:
        payload = job.payload
        camera_id = str(payload["camera_id"])
        duration_sec = int(payload.get("duration_sec") or self.settings.cameras[camera_id].default_duration_sec)
        pre_event_sec = int(payload.get("pre_event_sec") or 0)
        path = record_event_clip(
            self.settings,
            camera_id=camera_id,
            job_id=job.id,
            duration_sec=duration_sec,
            pre_event_sec=pre_event_sec,
            event_time_value=payload.get("event_time"),
        )
        session.add(
            Video(
                job_id=job.id,
                camera_id=camera_id,
                path=str(path),
                size_bytes=path.stat().st_size,
                duration_sec=duration_sec,
            )
        )
        session.commit()

        caption = payload.get("message") or f"Camera {camera_id}"
        message_thread_id = _message_thread_id(payload)
        for chat_id in _chat_ids(payload):
            self._require_telegram().send_video(
                chat_id,
                path,
                caption=caption,
                message_thread_id=message_thread_id,
            )

    def _process_snapshot(self, job: Job) -> None:
        payload = job.payload
        camera_id = str(payload["camera_id"])
        path = record_snapshot(self.settings, camera_id, job.id)
        caption = payload.get("message") or f"Snapshot {camera_id}"
        message_thread_id = _message_thread_id(payload)
        for chat_id in _chat_ids(payload):
            self._require_telegram().send_photo(
                chat_id,
                path,
                caption=caption,
                message_thread_id=message_thread_id,
            )

    def _process_home_assistant(self, job: Job) -> None:
        payload = job.payload
        result = self.ha.call_service(
            domain=str(payload["domain"]),
            service=str(payload["service"]),
            data=dict(payload.get("data") or {}),
        )
        message_thread_id = _message_thread_id(payload)
        for chat_id in _chat_ids(payload):
            self._require_telegram().send_message(
                chat_id,
                f"Home Assistant service executed: {payload['domain']}.{payload['service']}",
                message_thread_id=message_thread_id,
            )
        logger.debug("Home Assistant result for job %s: %s", job.id, result)

    def _process_send_message(self, job: Job) -> None:
        payload = job.payload
        text = str(payload.get("text") or "")
        if not text:
            raise ValueError("send_message job requires payload.text")
        message_thread_id = _message_thread_id(payload)
        for chat_id in _chat_ids(payload):
            self._require_telegram().send_message(chat_id, text, message_thread_id=message_thread_id)

    def _notify_failure(self, job: Job, error: str) -> None:
        chat_ids = _chat_ids(job.payload)
        if not chat_ids or self.telegram is None:
            return
        text = f"Job failed: {job.id}\n{error[:500]}"
        message_thread_id = _message_thread_id(job.payload)
        for chat_id in chat_ids:
            try:
                self.telegram.send_message(chat_id, text, message_thread_id=message_thread_id)
            except Exception:
                logger.exception("Failed to notify chat %s about job failure", chat_id)

    def _require_telegram(self) -> TelegramClient:
        if self.telegram is None:
            raise RuntimeError("Telegram bot token is not configured")
        return self.telegram


def _chat_ids(payload: dict[str, Any]) -> list[int]:
    return [int(chat_id) for chat_id in payload.get("chat_ids") or []]


def _message_thread_id(payload: dict[str, Any]) -> int | None:
    value = payload.get("message_thread_id")
    return int(value) if value is not None else None
