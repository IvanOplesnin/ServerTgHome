from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel

from server_tg_home.core.config import Settings, load_settings
from server_tg_home.core.logging import configure_logging
from server_tg_home.core.status import build_status_text
from server_tg_home.database.session import init_db, new_session
from server_tg_home.jobs.factory import create_event_job, create_record_video_job
from server_tg_home.jobs.queue import JobQueue
from server_tg_home.media.storage import ensure_storage
from server_tg_home.telegram.polling import TelegramPolling

logger = logging.getLogger(__name__)


class RecordVideoRequest(BaseModel):
    camera_id: str
    duration_sec: int | None = None
    pre_event_sec: int | None = None
    chat_ids: list[int] | None = None
    message_thread_id: int | None = None
    message: str | None = None


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = load_settings()
        configure_logging(settings.app.log_level)
        init_db(settings.app.database_url)
        ensure_storage(settings)
        queue = JobQueue(settings)

        app.state.settings = settings
        app.state.queue = queue
        app.state.telegram_polling = None
        app.state.telegram_task = None

        if settings.api.enable_telegram_polling and settings.telegram.bot_token:
            polling = TelegramPolling(settings, queue)
            task = asyncio.create_task(polling.run())
            app.state.telegram_polling = polling
            app.state.telegram_task = task
        else:
            logger.info("Telegram polling disabled or bot token is not configured")

        try:
            yield
        finally:
            polling = app.state.telegram_polling
            task = app.state.telegram_task
            if polling is not None:
                await polling.stop()
            if task is not None and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="Server Tg Home", lifespan=lifespan)

    @app.get("/health")
    def health(request: Request) -> dict[str, Any]:
        settings: Settings = request.app.state.settings
        queue: JobQueue = request.app.state.queue
        return {
            "status": "ok",
            "redis": queue.ping(),
            "queue_length": queue.length(),
            "cameras": list(settings.cameras.keys()),
            "events": list(settings.events.keys()),
        }

    @app.get("/status")
    def status(request: Request) -> dict[str, str]:
        settings: Settings = request.app.state.settings
        queue: JobQueue = request.app.state.queue
        with new_session() as session:
            return {"status": build_status_text(settings, session, queue)}

    @app.post("/events/{event_id}")
    async def receive_event(
        event_id: str,
        request: Request,
        x_webhook_token: str | None = Header(default=None, alias="X-Webhook-Token"),
    ) -> dict[str, str]:
        settings: Settings = request.app.state.settings
        queue: JobQueue = request.app.state.queue
        _verify_webhook_token(settings, x_webhook_token)

        event = settings.events.get(event_id)
        if event is None:
            raise HTTPException(status_code=404, detail=f"Unknown event: {event_id}")
        payload = await _json_or_empty(request)

        try:
            with new_session() as session:
                job_id = create_event_job(
                    settings,
                    session,
                    queue,
                    event_id=event_id,
                    event=event,
                    event_payload=payload,
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"job_id": job_id, "status": "queued"}

    @app.post("/jobs/record-video")
    async def record_video(
        body: RecordVideoRequest,
        request: Request,
        x_webhook_token: str | None = Header(default=None, alias="X-Webhook-Token"),
    ) -> dict[str, str]:
        settings: Settings = request.app.state.settings
        queue: JobQueue = request.app.state.queue
        _verify_webhook_token(settings, x_webhook_token)

        camera = settings.cameras.get(body.camera_id)
        if camera is None:
            raise HTTPException(status_code=404, detail=f"Unknown camera: {body.camera_id}")
        duration = body.duration_sec or camera.default_duration_sec
        try:
            with new_session() as session:
                job_id = create_record_video_job(
                    settings,
                    session,
                    queue,
                    source="http_api",
                    camera_id=body.camera_id,
                    duration_sec=max(1, min(duration, 300)),
                    pre_event_sec=body.pre_event_sec,
                    chat_ids=body.chat_ids,
                    message_thread_id=body.message_thread_id,
                    message=body.message,
                    event_payload={},
                )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"job_id": job_id, "status": "queued"}

    return app


def _verify_webhook_token(settings: Settings, token: str | None) -> None:
    if settings.app.webhook_token and token != settings.app.webhook_token:
        raise HTTPException(status_code=401, detail="Invalid webhook token")


async def _json_or_empty(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {"value": payload}
