from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import timedelta
from pathlib import Path

from aiogram import Dispatcher, F
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError
from aiogram.filters import Command, CommandStart
from aiogram.types import BotCommand, CallbackQuery, Message
from sqlalchemy import select

from server_tg_home.audio.service import make_voice_source_path
from server_tg_home.core.config import Settings, TelegramPanelConfig
from server_tg_home.core.runtime_state import (
    clear_notification_mute,
    mute_notifications,
    set_notifications_armed,
)
from server_tg_home.core.sensor_analytics import build_sensor_analytics_text
from server_tg_home.core.status import build_cameras_text, build_status_text, build_storage_text
from server_tg_home.core.temperatures import build_humidity_text, build_temperature_text
from server_tg_home.database.models import Video
from server_tg_home.database.session import new_session
from server_tg_home.jobs.factory import (
    create_camera_audio_job,
    create_home_assistant_service_job,
    create_record_video_job,
    create_sensor_graph_job,
    create_snapshot_job,
)
from server_tg_home.jobs.queue import JobQueue
from server_tg_home.telegram.client import AsyncTelegramClient, TelegramApiError, chat_is_allowed
from server_tg_home.telegram.panels import (
    PANEL_CALLBACK_PREFIX,
    build_panel_markup,
    build_panel_text,
    parse_panel_callback,
)

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
    ("temp", "Show room temperatures", "/temp"),
    ("humidity", "Show room humidity", "/humidity"),
    ("analytics", "Show sensor analytics", "/analytics [room|all] [window]"),
    ("graph", "Build and send sensor graph", "/graph [room|all] [window] [metric]"),
    ("disk", "Show storage usage", "/disk"),
    ("panel", "Create Telegram topic button panel", "/panel <id|all>"),
    ("ac_on", "Turn on a Home Assistant climate entity", "/ac_on <climate.entity_id>"),
    ("status", "Show service status", "/status"),
)


class TelegramPolling:
    def __init__(
        self,
        settings: Settings,
        queue: JobQueue,
        graph_queue: JobQueue,
        audio_queue: JobQueue,
    ) -> None:
        self.settings = settings
        self.queue = queue
        self.graph_queue = graph_queue
        self.audio_queue = audio_queue
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
                allowed_updates=["message", "callback_query"],
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
            user_id = _message_user_id(message)
            text = (
                f"Chat id: {chat_id}\n"
                "Add it to telegram.allowed_chat_ids and telegram.default_chat_ids."
            )
            if user_id is not None:
                text += f"\nUser id: {user_id}\nUse it in telegram.admin_user_ids for admin-only actions."
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
            context = await self._admin_chat_context(message, "record camera clips")
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
            context = await self._admin_chat_context(message, "send saved camera videos")
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_last(chat_id, message_thread_id, _message_args(message))

        @self.dispatcher.message(Command("snapshot"))
        async def command_snapshot(message: Message) -> None:
            context = await self._admin_chat_context(message, "capture camera snapshots")
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_snapshot(chat_id, message_thread_id, _message_args(message))

        @self.dispatcher.message(Command("arm"))
        async def command_arm(message: Message) -> None:
            context = await self._admin_chat_context(message, "change notification state")
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_arm(chat_id, message_thread_id, _message_args(message))

        @self.dispatcher.message(Command("disarm"))
        async def command_disarm(message: Message) -> None:
            context = await self._admin_chat_context(message, "change notification state")
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_disarm(chat_id, message_thread_id, _message_args(message))

        @self.dispatcher.message(Command("mute"))
        async def command_mute(message: Message) -> None:
            context = await self._admin_chat_context(message, "mute notifications")
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_mute(chat_id, message_thread_id, _message_args(message))

        @self.dispatcher.message(Command("ac_on"))
        async def command_ac_on(message: Message) -> None:
            context = await self._admin_chat_context(message, "control Home Assistant")
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_ac_on(chat_id, message_thread_id, _message_args(message))

        @self.dispatcher.message(Command("temp", "temperature"))
        async def command_temperature(message: Message) -> None:
            context = await self._allowed_chat_context(message)
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_temperature(chat_id, message_thread_id, _message_args(message))

        @self.dispatcher.message(Command("humidity"))
        async def command_humidity(message: Message) -> None:
            context = await self._allowed_chat_context(message)
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_humidity(chat_id, message_thread_id, _message_args(message))

        @self.dispatcher.message(Command("analytics"))
        async def command_analytics(message: Message) -> None:
            context = await self._allowed_chat_context(message)
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_analytics(chat_id, message_thread_id, _message_args(message))

        @self.dispatcher.message(Command("graph"))
        async def command_graph(message: Message) -> None:
            context = await self._allowed_chat_context(message)
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_graph(chat_id, message_thread_id, _message_args(message))

        @self.dispatcher.message(Command("disk"))
        async def command_disk(message: Message) -> None:
            context = await self._allowed_chat_context(message)
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_disk(chat_id, message_thread_id, _message_args(message))

        @self.dispatcher.message(Command("panel"))
        async def command_panel(message: Message) -> None:
            context = await self._admin_chat_context(message, "create Telegram panels")
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_panel(chat_id, message_thread_id, _message_args(message))

        @self.dispatcher.message(Command("status"))
        async def command_status(message: Message) -> None:
            context = await self._allowed_chat_context(message)
            if context is None:
                return
            chat_id, message_thread_id = context
            await self._handle_status(chat_id, message_thread_id, _message_args(message))

        @self.dispatcher.callback_query(F.data.startswith(f"{PANEL_CALLBACK_PREFIX}:"))
        async def panel_callback(callback: CallbackQuery) -> None:
            await self._handle_panel_callback(callback)

        @self.dispatcher.message(F.voice)
        async def voice_message(message: Message) -> None:
            await self._handle_voice_message(message)

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

    async def _admin_chat_context(self, message: Message, action: str) -> tuple[int, int | None] | None:
        context = await self._allowed_chat_context(message)
        if context is None:
            return None
        chat_id, message_thread_id = context
        user_id = _message_user_id(message)
        if not _user_is_admin(self.settings, user_id):
            await self._reply(
                chat_id,
                f"User id {user_id or 'unknown'} is not allowed to {action}.",
                message_thread_id=message_thread_id,
            )
            return None
        return context

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

    async def _handle_temperature(self, chat_id: int, message_thread_id: int | None, args: list[str]) -> None:
        with new_session() as session:
            text = build_temperature_text(self.settings, session, html=True)
        await self._reply(chat_id, text, message_thread_id=message_thread_id, parse_mode="HTML")

    async def _handle_humidity(self, chat_id: int, message_thread_id: int | None, args: list[str]) -> None:
        with new_session() as session:
            text = build_humidity_text(self.settings, session, html=True)
        await self._reply(chat_id, text, message_thread_id=message_thread_id, parse_mode="HTML")

    async def _handle_analytics(self, chat_id: int, message_thread_id: int | None, args: list[str]) -> None:
        parsed = _parse_analytics_args(self.settings, args)
        if isinstance(parsed, str):
            await self._reply(chat_id, parsed, message_thread_id=message_thread_id)
            return
        room_id, window = parsed
        with new_session() as session:
            text = build_sensor_analytics_text(
                self.settings,
                session,
                room_id=room_id,
                window=window,
                html=True,
            )
        await self._reply(chat_id, text, message_thread_id=message_thread_id, parse_mode="HTML")

    async def _handle_graph(self, chat_id: int, message_thread_id: int | None, args: list[str]) -> None:
        parsed = _parse_graph_args(self.settings, args)
        if isinstance(parsed, str):
            await self._reply(chat_id, parsed, message_thread_id=message_thread_id)
            return
        room_id, window_sec, metrics = parsed
        with new_session() as session:
            job_id = create_sensor_graph_job(
                self.settings,
                session,
                self.graph_queue,
                source="telegram_command",
                room_id=room_id,
                metrics=metrics,
                window_sec=window_sec,
                chat_ids=[chat_id],
                message_thread_id=message_thread_id,
            )
        await self._reply(chat_id, f"Graph job queued: {job_id}", message_thread_id=message_thread_id)

    async def _handle_disk(self, chat_id: int, message_thread_id: int | None, args: list[str]) -> None:
        await self._reply(chat_id, build_storage_text(self.settings), message_thread_id=message_thread_id)

    async def _handle_panel(self, chat_id: int, message_thread_id: int | None, args: list[str]) -> None:
        if not self.settings.telegram.panels:
            await self._reply(
                chat_id,
                "No telegram.panels configured.",
                message_thread_id=message_thread_id,
            )
            return
        if not args:
            known = ", ".join(self.settings.telegram.panels.keys())
            await self._reply(
                chat_id,
                f"Usage: /panel <id|all>. Available: {known}",
                message_thread_id=message_thread_id,
            )
            return

        panel_ids = list(self.settings.telegram.panels.keys()) if args[0].lower() == "all" else [args[0]]
        sent: list[str] = []
        for panel_id in panel_ids:
            panel = self.settings.telegram.panels.get(panel_id)
            if panel is None:
                await self._reply(chat_id, f"Unknown panel: {panel_id}", message_thread_id=message_thread_id)
                continue
            target_chat_id = panel.chat_id or chat_id
            target_thread_id = panel.message_thread_id if panel.message_thread_id is not None else message_thread_id
            if not chat_is_allowed(self.settings.telegram, target_chat_id):
                await self._reply(
                    chat_id,
                    f"Panel {panel_id} target chat {target_chat_id} is not allowed.",
                    message_thread_id=message_thread_id,
                )
                continue
            try:
                await self.client.send_message(
                    target_chat_id,
                    build_panel_text(panel_id, panel),
                    message_thread_id=target_thread_id,
                    parse_mode="HTML",
                    reply_markup=build_panel_markup(panel_id, panel),
                )
            except (TelegramApiError, TelegramAPIError, TelegramNetworkError):
                logger.warning("Failed to send Telegram panel %s", panel_id, exc_info=True)
                await self._reply(chat_id, f"Failed to send panel: {panel_id}", message_thread_id=message_thread_id)
                continue
            sent.append(panel_id)

        if sent:
            await self._reply(chat_id, f"Panels sent: {', '.join(sent)}", message_thread_id=message_thread_id)

    async def _handle_panel_callback(self, callback: CallbackQuery) -> None:
        parsed = parse_panel_callback(callback.data or "")
        if parsed is None:
            await _answer_callback(callback, "Unknown panel action.", alert=True)
            return

        chat_id, message_thread_id = _callback_chat_context(callback)
        if chat_id is None:
            await _answer_callback(callback, "Message context is unavailable.", alert=True)
            return
        if not chat_is_allowed(self.settings.telegram, chat_id):
            await _answer_callback(callback, f"Chat id {chat_id} is not allowed.", alert=True)
            return

        panel_id, action = parsed
        panel = self.settings.telegram.panels.get(panel_id)
        if panel is None:
            await _answer_callback(callback, f"Unknown panel: {panel_id}", alert=True)
            return
        if panel.kind == "door" and not _user_is_admin(self.settings, _callback_user_id(callback)):
            await _answer_callback(callback, "This camera action is admin-only.", alert=True)
            return

        await _answer_callback(callback, "Выполняю")
        try:
            await self._execute_panel_action(panel_id, panel, action, chat_id, message_thread_id)
        except ValueError as exc:
            await self._reply(chat_id, str(exc), message_thread_id=message_thread_id)

    async def _execute_panel_action(
        self,
        panel_id: str,
        panel: TelegramPanelConfig,
        action: str,
        chat_id: int,
        message_thread_id: int | None,
    ) -> None:
        if panel.kind == "door":
            if action == "clip":
                await self._queue_panel_clip(panel_id, panel, chat_id, message_thread_id)
                return
            if action == "snapshot":
                await self._queue_panel_snapshot(panel_id, panel, chat_id, message_thread_id)
                return
        if panel.kind == "climate":
            if action == "current":
                await self._handle_temperature(chat_id, message_thread_id, [])
                return
            graph_window = _panel_graph_window(action)
            if graph_window is not None:
                await self._queue_panel_graph(panel_id, panel, graph_window, chat_id, message_thread_id)
                return
        raise ValueError(f"Unsupported panel action: {action}")

    async def _queue_panel_clip(
        self,
        panel_id: str,
        panel: TelegramPanelConfig,
        chat_id: int,
        message_thread_id: int | None,
    ) -> None:
        camera_id = _panel_camera_id(panel_id, panel)
        duration = max(1, min(int(panel.video_duration_sec), 300))
        with new_session() as session:
            job_id = create_record_video_job(
                self.settings,
                session,
                self.queue,
                source="telegram_panel",
                camera_id=camera_id,
                duration_sec=duration,
                pre_event_sec=self.settings.buffer.pre_event_seconds,
                chat_ids=[chat_id],
                message_thread_id=message_thread_id,
                message=f"{panel.title}: видео {duration} сек",
            )
        await self._reply(chat_id, f"Video job queued: {job_id}", message_thread_id=message_thread_id)

    async def _queue_panel_snapshot(
        self,
        panel_id: str,
        panel: TelegramPanelConfig,
        chat_id: int,
        message_thread_id: int | None,
    ) -> None:
        camera_id = _panel_camera_id(panel_id, panel)
        with new_session() as session:
            job_id = create_snapshot_job(
                self.settings,
                session,
                self.queue,
                source="telegram_panel",
                camera_id=camera_id,
                chat_ids=[chat_id],
                message_thread_id=message_thread_id,
                message=f"{panel.title}: фото",
            )
        await self._reply(chat_id, f"Snapshot job queued: {job_id}", message_thread_id=message_thread_id)

    async def _queue_panel_graph(
        self,
        panel_id: str,
        panel: TelegramPanelConfig,
        window: tuple[str, int],
        chat_id: int,
        message_thread_id: int | None,
    ) -> None:
        room_id = str(panel.room_id or "all")
        window_label, window_sec = window
        with new_session() as session:
            job_id = create_sensor_graph_job(
                self.settings,
                session,
                self.graph_queue,
                source="telegram_panel",
                room_id=room_id,
                metrics=["temperature", "humidity"],
                window_sec=window_sec,
                chat_ids=[chat_id],
                message_thread_id=message_thread_id,
            )
        await self._reply(
            chat_id,
            f"Graph {window_label} job queued: {job_id}",
            message_thread_id=message_thread_id,
        )

    async def _handle_status(self, chat_id: int, message_thread_id: int | None, args: list[str]) -> None:
        with new_session() as session:
            text = build_status_text(
                self.settings,
                session,
                self.queue,
                extra_queues={"graph": self.graph_queue, "audio": self.audio_queue},
            )
        await self._reply(chat_id, text, message_thread_id=message_thread_id)

    async def _handle_voice_message(self, message: Message) -> None:
        context = await self._allowed_chat_context(message)
        if context is None:
            return
        chat_id, message_thread_id = context
        user_id = _message_user_id(message)
        if not _user_is_explicit_admin(self.settings, user_id):
            await self._reply(
                chat_id,
                f"User id {user_id or 'unknown'} is not allowed to play voice messages on camera speakers.",
                message_thread_id=message_thread_id,
            )
            return
        if not self.settings.audio.enabled:
            await self._reply(chat_id, "Audio playback is disabled.", message_thread_id=message_thread_id)
            return

        topic_id, camera_id = _resolve_camera_topic(self.settings, chat_id, message_thread_id)
        if camera_id is None:
            await self._reply(
                chat_id,
                "This topic is not mapped to a camera speaker.",
                message_thread_id=message_thread_id,
            )
            return

        camera = self.settings.cameras.get(camera_id)
        if camera is None:
            await self._reply(chat_id, f"Unknown camera: {camera_id}", message_thread_id=message_thread_id)
            return
        if not camera.speaker_enabled:
            await self._reply(
                chat_id,
                f"Camera speaker is not enabled: {camera_id}",
                message_thread_id=message_thread_id,
            )
            return

        voice = message.voice
        if voice is None:
            return
        duration_sec = int(voice.duration or 0)
        if duration_sec <= 0:
            await self._reply(chat_id, "Voice message duration is unavailable.", message_thread_id=message_thread_id)
            return
        if duration_sec > self.settings.audio.max_duration_sec:
            await self._reply(
                chat_id,
                f"Voice message is too long. Max: {self.settings.audio.max_duration_sec}s.",
                message_thread_id=message_thread_id,
            )
            return

        source_path = make_voice_source_path(
            self.settings,
            camera_id=camera_id,
            chat_id=chat_id,
            message_thread_id=message_thread_id,
            message_id=message.message_id,
            file_unique_id=voice.file_unique_id,
        )
        try:
            telegram_file = await self.client.bot.get_file(voice.file_id)
            if telegram_file.file_path is None:
                raise RuntimeError("Telegram did not return a file path.")
            await self.client.bot.download_file(telegram_file.file_path, destination=source_path)
        except Exception:
            logger.exception("Failed to download Telegram voice message")
            await self._reply(
                chat_id,
                "Failed to download voice message.",
                message_thread_id=message_thread_id,
            )
            return

        with new_session() as session:
            try:
                job_id = create_camera_audio_job(
                    self.settings,
                    session,
                    self.audio_queue,
                    source="telegram_voice",
                    camera_id=camera_id,
                    source_path=str(source_path),
                    duration_sec=duration_sec,
                    chat_ids=[chat_id],
                    message_thread_id=message_thread_id,
                    telegram_file_id=voice.file_id,
                    telegram_file_unique_id=voice.file_unique_id,
                    telegram_message_id=message.message_id,
                    sender_user_id=_message_user_id(message),
                    sender_name=_message_sender_name(message),
                )
            except ValueError as exc:
                await self._reply(chat_id, str(exc), message_thread_id=message_thread_id)
                return

        suffix = f" ({topic_id})" if topic_id else ""
        await self._reply(
            chat_id,
            f"Голосовое принято для камеры {camera_id}{suffix}.\nJob: {job_id}",
            message_thread_id=message_thread_id,
        )

    async def _reply(
        self,
        chat_id: int,
        text: str,
        message_thread_id: int | None = None,
        parse_mode: str | None = None,
    ) -> None:
        with suppress(TelegramApiError, TelegramAPIError, TelegramNetworkError):
            await self.client.send_message(
                chat_id,
                text,
                message_thread_id=message_thread_id,
                parse_mode=parse_mode,
            )


def _message_chat_id(message: Message) -> int | None:
    chat_id = message.chat.id
    return chat_id if isinstance(chat_id, int) else None


def _message_thread_id(message: Message) -> int | None:
    message_thread_id = getattr(message, "message_thread_id", None)
    return message_thread_id if isinstance(message_thread_id, int) else None


def _message_user_id(message: Message) -> int | None:
    user = getattr(message, "from_user", None)
    user_id = getattr(user, "id", None)
    return user_id if isinstance(user_id, int) else None


def _message_sender_name(message: Message) -> str | None:
    user = getattr(message, "from_user", None)
    if user is None:
        return None
    full_name = getattr(user, "full_name", None)
    if isinstance(full_name, str) and full_name:
        return full_name
    username = getattr(user, "username", None)
    return str(username) if username else None


def _message_args(message: Message) -> list[str]:
    text = message.text or ""
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return []
    return parts[1].split()


async def _answer_callback(callback: CallbackQuery, text: str, *, alert: bool = False) -> None:
    with suppress(TelegramAPIError, TelegramNetworkError):
        await callback.answer(text, show_alert=alert)


def _callback_chat_context(callback: CallbackQuery) -> tuple[int | None, int | None]:
    message = callback.message
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    if not isinstance(chat_id, int):
        return None, None
    message_thread_id = getattr(message, "message_thread_id", None)
    if not isinstance(message_thread_id, int):
        message_thread_id = None
    return chat_id, message_thread_id


def _callback_user_id(callback: CallbackQuery) -> int | None:
    user = getattr(callback, "from_user", None)
    user_id = getattr(user, "id", None)
    return user_id if isinstance(user_id, int) else None


def _user_is_admin(settings: Settings, user_id: int | None) -> bool:
    if not settings.telegram.admin_user_ids:
        return True
    return user_id in settings.telegram.admin_user_ids


def _user_is_explicit_admin(settings: Settings, user_id: int | None) -> bool:
    return bool(settings.telegram.admin_user_ids) and user_id in settings.telegram.admin_user_ids


def _resolve_camera_topic(
    settings: Settings,
    chat_id: int,
    message_thread_id: int | None,
) -> tuple[str | None, str | None]:
    for topic_id, topic in settings.telegram.camera_topics.items():
        if topic.chat_id is None:
            continue
        if topic.chat_id != chat_id:
            continue
        if topic.message_thread_id != message_thread_id:
            continue
        return topic_id, topic.camera_id
    return None, None


def _panel_camera_id(panel_id: str, panel: TelegramPanelConfig) -> str:
    if not panel.camera_id:
        raise ValueError(f"Panel {panel_id} requires camera_id.")
    return panel.camera_id


def _panel_graph_window(action: str) -> tuple[str, int] | None:
    windows = {
        "graph_6h": ("6h", 6 * 3600),
        "graph_12h": ("12h", 12 * 3600),
        "graph_24h": ("24h", 24 * 3600),
        "graph_7d": ("7d", 7 * 86400),
        "graph_30d": ("30d", 30 * 86400),
    }
    return windows.get(action)


def _parse_analytics_args(settings: Settings, args: list[str]) -> tuple[str, timedelta] | str:
    remaining = list(args)
    room_id = "all"
    if remaining:
        candidate = remaining[0].lower()
        if candidate == "all" or candidate in settings.temperatures.rooms:
            room_id = candidate
            remaining.pop(0)
        elif _parse_duration(candidate) is None:
            known = ", ".join(["all", *settings.temperatures.rooms.keys()])
            return f"Unknown room: {candidate}. Available: {known}"

    default_window = _parse_duration(settings.graphs.default_window) or timedelta(hours=24)
    window = default_window
    if remaining:
        candidate_window = _parse_duration(remaining[0])
        if candidate_window is None:
            return "Usage: /analytics [room|all] [window]"
        window = candidate_window
        remaining.pop(0)

    max_window = _parse_duration(settings.graphs.max_window) or timedelta(days=30)
    if window > max_window:
        return f"Window is too large. Max: {settings.graphs.max_window}."
    if remaining:
        return "Usage: /analytics [room|all] [window]"
    return room_id, window


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


def _parse_graph_args(settings: Settings, args: list[str]) -> tuple[str, int, list[str]] | str:
    remaining = list(args)
    room_id = "all"
    if remaining:
        candidate = remaining[0].lower()
        if candidate == "all" or candidate in settings.temperatures.rooms:
            room_id = candidate
            remaining.pop(0)
        elif _parse_duration(candidate) is None:
            known = ", ".join(["all", *settings.temperatures.rooms.keys()])
            return f"Unknown room: {candidate}. Available: {known}"

    default_window = _parse_duration(settings.graphs.default_window) or timedelta(hours=24)
    window = default_window
    if remaining:
        candidate_window = _parse_duration(remaining[0])
        if candidate_window is not None:
            window = candidate_window
            remaining.pop(0)

    max_window = _parse_duration(settings.graphs.max_window) or timedelta(days=30)
    if window > max_window:
        return f"Window is too large. Max: {settings.graphs.max_window}."

    metrics = ["temperature", "humidity"]
    if remaining:
        metric = remaining.pop(0).lower()
        parsed_metrics = _parse_graph_metrics(metric)
        if parsed_metrics is None:
            return "Metric must be temperature, humidity or all."
        metrics = parsed_metrics

    if remaining:
        return "Usage: /graph [room|all] [window] [temperature|humidity|all]"

    return room_id, max(60, int(window.total_seconds())), metrics


def _parse_graph_metrics(value: str) -> list[str] | None:
    if value in {"all", "climate", "both"}:
        return ["temperature", "humidity"]
    if value in {"temp", "temperature", "t"}:
        return ["temperature"]
    if value in {"hum", "humidity", "h"}:
        return ["humidity"]
    return None
