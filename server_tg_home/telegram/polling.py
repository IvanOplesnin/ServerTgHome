from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import timedelta
from pathlib import Path

from aiogram import Dispatcher, F
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, Message
from sqlalchemy import select

from server_tg_home.core.config import Settings
from server_tg_home.core.runtime_state import (
    clear_notification_mute,
    mute_notifications,
    set_notifications_armed,
)
from server_tg_home.core.status import build_cameras_text, build_status_text
from server_tg_home.database.models import Video
from server_tg_home.database.session import new_session
from server_tg_home.jobs.factory import (
    create_home_assistant_service_job,
    create_record_video_job,
    create_snapshot_job,
)
from server_tg_home.jobs.queue import JobQueue
from server_tg_home.telegram.client import AsyncTelegramClient, TelegramApiError, chat_is_allowed

logger = logging.getLogger(__name__)


TELEGRAM_COMMANDS: tuple[tuple[str, str, str], ...] = (
    ("start", "Show chat and topic id", "/start"),
    ("help", "Show available commands", "/help"),
    ("cameras", "Show camera and buffer status", "/cameras"),
    ("clip", "Record and send a camera clip", "/clip <camera> [seconds]"),
    ("last", "Send latest saved video", "/last [camera]"),
    ("snapshot", "Capture and send one camera frame", "/snapshot <camera>"),
    ("arm", "Enable automatic event notifications", "/arm"),
    ("disarm", "Disable automatic event notifications", "/disarm"),
    ("mute", "Mute event notifications temporarily", "/mute <duration|off>"),
    ("ac_on", "Turn on a Home Assistant climate entity", "/ac_on <climate.entity_id>"),
    ("status", "Show service status", "/status"),
)


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
            await self._setup_bot_commands()
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

    async def _setup_bot_commands(self) -> None:
        commands = [
            BotCommand(command=command, description=description)
            for command, description, _ in TELEGRAM_COMMANDS
        ]
        try:
            await self.client.bot.set_my_commands(commands)
            logger.info("Telegram bot command menu configured")
        except (TelegramAPIError, TelegramNetworkError):
            logger.warning("Failed to configure Telegram bot commands", exc_info=True)

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

        @self.dispatcher.message(Command("cameras"))
        async def command_cameras(message: Message) -> None:
            context = await self._allowed_chat_context(message)
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_cameras(chat_id, message_thread_id, _message_args(message))

        @self.dispatcher.message(Command("last"))
        async def command_last(message: Message) -> None:
            context = await self._allowed_chat_context(message)
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_last(chat_id, message_thread_id, _message_args(message))

        @self.dispatcher.message(Command("snapshot"))
        async def command_snapshot(message: Message) -> None:
            context = await self._allowed_chat_context(message)
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_snapshot(chat_id, message_thread_id, _message_args(message))

        @self.dispatcher.message(Command("arm"))
        async def command_arm(message: Message) -> None:
            context = await self._allowed_chat_context(message)
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_arm(chat_id, message_thread_id, _message_args(message))

        @self.dispatcher.message(Command("disarm"))
        async def command_disarm(message: Message) -> None:
            context = await self._allowed_chat_context(message)
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_disarm(chat_id, message_thread_id, _message_args(message))

        @self.dispatcher.message(Command("mute"))
        async def command_mute(message: Message) -> None:
            context = await self._allowed_chat_context(message)
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_mute(chat_id, message_thread_id, _message_args(message))

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
        lines = ["Commands:"]
        lines.extend(f"{usage} - {description}" for _, description, usage in TELEGRAM_COMMANDS)
        await self._reply(
            chat_id,
            "\n".join(lines),
            message_thread_id=message_thread_id,
        )

    async def _handle_cameras(self, chat_id: int, message_thread_id: int | None, args: list[str]) -> None:
        await self._reply(chat_id, build_cameras_text(self.settings), message_thread_id=message_thread_id)

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

    async def _handle_last(self, chat_id: int, message_thread_id: int | None, args: list[str]) -> None:
        camera_id = args[0] if args else None
        if camera_id is not None and camera_id not in self.settings.cameras:
            await self._reply(chat_id, f"Unknown camera: {camera_id}", message_thread_id=message_thread_id)
            return

        with new_session() as session:
            query = select(Video).where(Video.deleted_at.is_(None)).order_by(Video.created_at.desc()).limit(20)
            if camera_id is not None:
                query = (
                    select(Video)
                    .where(Video.deleted_at.is_(None), Video.camera_id == camera_id)
                    .order_by(Video.created_at.desc())
                    .limit(20)
                )
            videos = session.execute(query).scalars().all()

        for video in videos:
            path = Path(video.path)
            if path.exists():
                await self.client.send_video(
                    chat_id,
                    path,
                    caption=f"Last video {video.camera_id}",
                    message_thread_id=message_thread_id,
                )
                return
        await self._reply(chat_id, "No saved video found.", message_thread_id=message_thread_id)

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

    async def _handle_arm(self, chat_id: int, message_thread_id: int | None, args: list[str]) -> None:
        with new_session() as session:
            set_notifications_armed(session, True)
            session.commit()
        await self._reply(chat_id, "Automatic event notifications armed.", message_thread_id=message_thread_id)

    async def _handle_disarm(self, chat_id: int, message_thread_id: int | None, args: list[str]) -> None:
        with new_session() as session:
            set_notifications_armed(session, False)
            session.commit()
        await self._reply(chat_id, "Automatic event notifications disarmed.", message_thread_id=message_thread_id)

    async def _handle_mute(self, chat_id: int, message_thread_id: int | None, args: list[str]) -> None:
        if not args:
            await self._reply(chat_id, "Usage: /mute <duration|off>, example: /mute 1h", message_thread_id=message_thread_id)
            return
        if args[0].lower() in {"off", "clear", "0"}:
            with new_session() as session:
                clear_notification_mute(session)
                session.commit()
            await self._reply(chat_id, "Notification mute cleared.", message_thread_id=message_thread_id)
            return

        duration = _parse_duration(args[0])
        if duration is None:
            await self._reply(chat_id, "Duration must look like 30m, 1h or 2d.", message_thread_id=message_thread_id)
            return
        with new_session() as session:
            muted_until = mute_notifications(session, duration)
            session.commit()
        await self._reply(
            chat_id,
            f"Event notifications muted until {muted_until.isoformat()}.",
            message_thread_id=message_thread_id,
        )

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


def _parse_duration(value: str) -> timedelta | None:
    value = value.strip().lower()
    if not value:
        return None
    unit = value[-1]
    number = value[:-1]
    if unit not in {"s", "m", "h", "d"} or not number.isdigit():
        return None
    amount = int(number)
    if amount <= 0:
        return None
    if unit == "s":
        return timedelta(seconds=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    return timedelta(days=amount)
