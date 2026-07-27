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
from server_tg_home.core.temperatures import update_temperatures_from_payload
from server_tg_home.database.session import init_db, new_session
from server_tg_home.jobs.factory import create_event_job, create_record_video_job
from server_tg_home.jobs.queue import JobQueue
from server_tg_home.media.storage import ensure_storage
from server_tg_home.telegram.client import create_aiogram_bot
from server_tg_home.telegram.polling import TelegramPolling
from server_tg_home.webapp.auth import AiogramMembershipChecker, MiniAppAuthService
from server_tg_home.webapp.control import create_webapp_control_router
from server_tg_home.webapp.dependencies import require_webapp_session
from server_tg_home.webapp.router import create_webapp_router
from server_tg_home.webapp.session import RedisSessionStore
from server_tg_home.webapp.tickets import RedisTicketStore

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
        graph_queue = JobQueue(
            settings,
            queue_name=settings.graphs.queue_name,
            enqueue_func=_enqueue_graph_job,
        )
        audio_queue = JobQueue(
            settings,
            queue_name=settings.audio.queue_name,
            enqueue_func=_enqueue_audio_job,
        )

        app.state.settings = settings
        app.state.queue = queue
        app.state.graph_queue = graph_queue
        app.state.audio_queue = audio_queue
        app.state.telegram_polling = None
        app.state.telegram_task = None
        app.state.webapp_auth = None
        app.state.webapp_tickets = None
        webapp_session_store: RedisSessionStore | None = None
        webapp_ticket_store: RedisTicketStore | None = None
        membership_bot = None

        if settings.api.enable_telegram_polling and settings.telegram.bot_token:
            polling = TelegramPolling(settings, queue, graph_queue, audio_queue)
            task = asyncio.create_task(polling.run())
            app.state.telegram_polling = polling
            app.state.telegram_task = task
        else:
            logger.info("Telegram polling disabled or bot token is not configured")

        if settings.webapp.enabled:
            webapp_session_store = RedisSessionStore.from_url(
                settings.app.redis_url,
                settings.webapp.session_ttl_sec,
            )
            webapp_ticket_store = RedisTicketStore.from_url(
                settings.app.redis_url,
            )
            membership_checker = None
            if settings.telegram.bot_token:
                polling = app.state.telegram_polling
                if polling is not None:
                    membership_bot = polling.client.bot
                else:
                    membership_bot = create_aiogram_bot(settings.telegram)
                membership_checker = AiogramMembershipChecker(membership_bot)
            else:
                logger.warning(
                    "Telegram Mini App is enabled without a bot token; "
                    "authentication will fail closed"
                )
            app.state.webapp_auth = MiniAppAuthService(
                settings,
                webapp_session_store,
                membership_checker,
            )
            app.state.webapp_tickets = webapp_ticket_store

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
            if (
                membership_bot is not None
                and polling is None
            ):
                await membership_bot.session.close()
            if webapp_session_store is not None:
                await webapp_session_store.close()
            if webapp_ticket_store is not None:
                await webapp_ticket_store.close()

    app = FastAPI(title="Server Tg Home", lifespan=lifespan)

    @app.get("/health")
    def health(request: Request) -> dict[str, Any]:
        settings: Settings = request.app.state.settings
        queue: JobQueue = request.app.state.queue
        graph_queue: JobQueue = request.app.state.graph_queue
        audio_queue: JobQueue = request.app.state.audio_queue
        return {
            "status": "ok",
            "redis": queue.ping(),
            "queue_length": queue.length(),
            "graph_queue_length": graph_queue.length(),
            "audio_queue_length": audio_queue.length(),
            "cameras": list(settings.cameras.keys()),
            "events": list(settings.events.keys()),
            "temperature_rooms": list(settings.temperatures.rooms.keys()),
            "humidity_rooms": list(settings.temperatures.rooms.keys()),
        }

    @app.get("/status")
    def status(request: Request) -> dict[str, str]:
        settings: Settings = request.app.state.settings
        queue: JobQueue = request.app.state.queue
        graph_queue: JobQueue = request.app.state.graph_queue
        audio_queue: JobQueue = request.app.state.audio_queue
        with new_session() as session:
            return {
                "status": build_status_text(
                    settings,
                    session,
                    queue,
                    extra_queues={"graph": graph_queue, "audio": audio_queue},
                )
            }

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
        if job_id is None:
            return {"job_id": "", "status": "ignored"}
        return {"job_id": job_id, "status": "queued"}

    @app.post("/webhooks/humidity")
    @app.post("/webhooks/humidities")
    @app.post("/webhooks/temperature")
    @app.post("/webhooks/temperatures")
    async def receive_temperatures(
        request: Request,
        x_webhook_token: str | None = Header(default=None, alias="X-Webhook-Token"),
    ) -> dict[str, Any]:
        settings: Settings = request.app.state.settings
        _verify_webhook_token(settings, x_webhook_token)
        payload = await _json_or_empty(request)
        default_metric = "humidity" if request.url.path in {"/webhooks/humidity", "/webhooks/humidities"} else "temperature"

        try:
            with new_session() as session:
                result = update_temperatures_from_payload(
                    session,
                    settings,
                    payload,
                    default_metric=default_metric,
                )
                session.commit()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "status": "updated",
            "updated_rooms": result.updated,
            "skipped_rooms": result.skipped,
            "updated_humidity_rooms": result.updated_humidity,
            "skipped_humidity_rooms": result.skipped_humidity,
        }

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

    app.include_router(create_webapp_control_router())
    app.include_router(
        create_webapp_router(auth_dependency=require_webapp_session)
    )

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


def _enqueue_graph_job(job_id: str) -> None:
    from server_tg_home.jobs.graph_tasks import enqueue_graph_job

    enqueue_graph_job(job_id)


def _enqueue_audio_job(job_id: str) -> None:
    from server_tg_home.jobs.audio_tasks import enqueue_audio_job

    enqueue_audio_job(job_id)
