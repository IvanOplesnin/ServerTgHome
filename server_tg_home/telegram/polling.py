from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from aiogram import Dispatcher, F
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from server_tg_home.core.config import Settings
from server_tg_home.core.status import build_status_text
from server_tg_home.database.session import new_session
from server_tg_home.jobs.factory import (
    create_home_assistant_service_job,
    create_record_video_job,
    create_snapshot_job,
)
from server_tg_home.jobs.queue import JobQueue
from server_tg_home.telegram.client import AsyncTelegramClient, TelegramApiError, chat_is_allowed

logger = logging.getLogger(__name__)


class TelegramPolling:
    def __init__(self, settings: Settings, queue: JobQueue) -> None:
        self.settings = settings
        self.queue = queue
        self.client = AsyncTelegramClient(settings.telegram)
        self.dispatcher = Dispatcher()
        self._register_handlers()

    async def stop(self) -> None:
        with suppress(RuntimeError):
            await self.dispatcher.stop_polling()
        await self.client.close()

    async def run(self) -> None:
        logger.info("Telegram polling started with aiogram")
        try:
            await self.dispatcher.start_polling(
                self.client.bot,
                polling_timeout=self.settings.telegram.polling_timeout_sec,
                allowed_updates=["message"],
                handle_signals=False,
                close_bot_session=False,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Telegram polling failed")
            raise
        finally:
            logger.info("Telegram polling stopped")

    def _register_handlers(self) -> None:
        @self.dispatcher.message(CommandStart())
        async def command_start(message: Message) -> None:
            chat_id = _message_chat_id(message)
            if chat_id is None:
                return
            message_thread_id = _message_thread_id(message)
            text = (
                f"Chat id: {chat_id}\n"
                "Add it to telegram.allowed_chat_ids and telegram.default_chat_ids."
            )
            if message_thread_id is not None:
                text += (
                    f"\nTopic message_thread_id: {message_thread_id}\n"
                    "Use it as telegram.default_message_thread_id or events.<event>.message_thread_id."
                )
            await self._reply(
                chat_id,
                text,
                message_thread_id=message_thread_id,
            )

        @self.dispatcher.message(Command("help"))
        async def command_help(message: Message) -> None:
            context = await self._allowed_chat_context(message)
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_help(chat_id, message_thread_id, _message_args(message))

        @self.dispatcher.message(Command("clip"))
        async def command_clip(message: Message) -> None:
            context = await self._allowed_chat_context(message)
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_clip(chat_id, message_thread_id, _message_args(message))

        @self.dispatcher.message(Command("snapshot"))
        async def command_snapshot(message: Message) -> None:
            context = await self._allowed_chat_context(message)
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_snapshot(chat_id, message_thread_id, _message_args(message))

        @self.dispatcher.message(Command("ac_on"))
        async def command_ac_on(message: Message) -> None:
            context = await self._allowed_chat_context(message)
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_ac_on(chat_id, message_thread_id, _message_args(message))

        @self.dispatcher.message(Command("status"))
        async def command_status(message: Message) -> None:
            context = await self._allowed_chat_context(message)
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_status(chat_id, message_thread_id, _message_args(message))

        @self.dispatcher.message(F.text.startswith("/"))
        async def command_unknown(message: Message) -> None:
            context = await self._allowed_chat_context(message)
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._reply(chat_id, "Unknown command. Use /help.", message_thread_id=message_thread_id)

    async def _allowed_chat_context(self, message: Message) -> tuple[int, int | None] | None:
        chat_id = _message_chat_id(message)
        if chat_id is None:
            return None
        message_thread_id = _message_thread_id(message)
        if not chat_is_allowed(self.settings.telegram, chat_id):
            await self._reply(
                chat_id,
                f"Chat id {chat_id} is not allowed.",
                message_thread_id=message_thread_id,
            )
            return None
        return chat_id, message_thread_id

    async def _handle_help(self, chat_id: int, message_thread_id: int | None, args: list[str]) -> None:
        await self._reply(
            chat_id,
            "Commands:\n"
            "/clip <camera> [seconds]\n"
            "/snapshot <camera>\n"
            "/ac_on <climate.entity_id>\n"
            "/status",
            message_thread_id=message_thread_id,
        )

    async def _handle_clip(self, chat_id: int, message_thread_id: int | None, args: list[str]) -> None:
        if not args:
            await self._reply(chat_id, "Usage: /clip <camera> [seconds]", message_thread_id=message_thread_id)
            return
        camera_id = args[0]
        camera = self.settings.cameras.get(camera_id)
        if camera is None:
            await self._reply(chat_id, f"Unknown camera: {camera_id}", message_thread_id=message_thread_id)
            return
        try:
            duration = int(args[1]) if len(args) > 1 else camera.default_duration_sec
        except ValueError:
            await self._reply(
                chat_id,
                "Duration must be an integer number of seconds.",
                message_thread_id=message_thread_id,
            )
            return
        duration = max(1, min(duration, 300))

        with new_session() as session:
            job_id = create_record_video_job(
                self.settings,
                session,
                self.queue,
                source="telegram_command",
                camera_id=camera_id,
                duration_sec=duration,
                pre_event_sec=self.settings.buffer.pre_event_seconds,
                chat_ids=[chat_id],
                message_thread_id=message_thread_id,
                message=f"Camera {camera_id}",
            )
        await self._reply(chat_id, f"Clip job queued: {job_id}", message_thread_id=message_thread_id)

    async def _handle_snapshot(self, chat_id: int, message_thread_id: int | None, args: list[str]) -> None:
        if not args:
            await self._reply(chat_id, "Usage: /snapshot <camera>", message_thread_id=message_thread_id)
            return
        camera_id = args[0]
        if camera_id not in self.settings.cameras:
            await self._reply(chat_id, f"Unknown camera: {camera_id}", message_thread_id=message_thread_id)
            return
        with new_session() as session:
            job_id = create_snapshot_job(
                self.settings,
                session,
                self.queue,
                source="telegram_command",
                camera_id=camera_id,
                chat_ids=[chat_id],
                message_thread_id=message_thread_id,
                message=f"Snapshot {camera_id}",
            )
        await self._reply(chat_id, f"Snapshot job queued: {job_id}", message_thread_id=message_thread_id)

    async def _handle_ac_on(self, chat_id: int, message_thread_id: int | None, args: list[str]) -> None:
        if not args:
            await self._reply(chat_id, "Usage: /ac_on <climate.entity_id>", message_thread_id=message_thread_id)
            return
        entity_id = args[0]
        with new_session() as session:
            job_id = create_home_assistant_service_job(
                self.settings,
                session,
                self.queue,
                source="telegram_command",
                domain="climate",
                service="turn_on",
                data={"entity_id": entity_id},
                chat_ids=[chat_id],
                message_thread_id=message_thread_id,
            )
        await self._reply(chat_id, f"Home Assistant job queued: {job_id}", message_thread_id=message_thread_id)

    async def _handle_status(self, chat_id: int, message_thread_id: int | None, args: list[str]) -> None:
        with new_session() as session:
            text = build_status_text(self.settings, session, self.queue)
        await self._reply(chat_id, text, message_thread_id=message_thread_id)

    async def _reply(self, chat_id: int, text: str, message_thread_id: int | None = None) -> None:
        with suppress(TelegramApiError, TelegramAPIError, TelegramNetworkError):
            await self.client.send_message(chat_id, text, message_thread_id=message_thread_id)


def _message_chat_id(message: Message) -> int | None:
    chat_id = message.chat.id
    return chat_id if isinstance(chat_id, int) else None


def _message_thread_id(message: Message) -> int | None:
    message_thread_id = getattr(message, "message_thread_id", None)
    return message_thread_id if isinstance(message_thread_id, int) else None


def _message_args(message: Message) -> list[str]:
    text = message.text or ""
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return []
    return parts[1].split()
