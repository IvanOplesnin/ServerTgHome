from __future__ import annotations

import asyncio
from pathlib import Path

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError
from aiogram.types import FSInputFile

from server_tg_home.core.config import TelegramConfig


class TelegramApiError(RuntimeError):
    pass


def create_aiogram_bot(config: TelegramConfig) -> Bot:
    if not config.bot_token:
        raise ValueError("telegram.bot_token is required")
    session = AiohttpSession(proxy=config.proxy_url, timeout=float(config.request_timeout_sec))
    return Bot(token=config.bot_token, session=session)


class AsyncTelegramClient:
    def __init__(self, config: TelegramConfig) -> None:
        self.config = config
        self.bot = create_aiogram_bot(config)

    async def close(self) -> None:
        await self.bot.session.close()

    async def send_message(
        self,
        chat_id: int,
        text: str,
        message_thread_id: int | None = None,
    ) -> None:
        kwargs = {"chat_id": chat_id, "text": text}
        if message_thread_id is not None:
            kwargs["message_thread_id"] = message_thread_id
        await self._call(self.bot.send_message(**kwargs))

    async def send_video(
        self,
        chat_id: int,
        path: Path,
        caption: str | None = None,
        message_thread_id: int | None = None,
    ) -> None:
        video = FSInputFile(path)
        kwargs = {"chat_id": chat_id, "video": video, "caption": caption}
        if message_thread_id is not None:
            kwargs["message_thread_id"] = message_thread_id
        await self._call(self.bot.send_video(**kwargs))

    async def send_photo(
        self,
        chat_id: int,
        path: Path,
        caption: str | None = None,
        message_thread_id: int | None = None,
    ) -> None:
        photo = FSInputFile(path)
        kwargs = {"chat_id": chat_id, "photo": photo, "caption": caption}
        if message_thread_id is not None:
            kwargs["message_thread_id"] = message_thread_id
        await self._call(self.bot.send_photo(**kwargs))

    async def _call(self, awaitable) -> None:
        try:
            await awaitable
        except (TelegramAPIError, TelegramNetworkError) as exc:
            raise TelegramApiError(str(exc)) from exc


class TelegramClient:
    def __init__(self, config: TelegramConfig) -> None:
        if not config.bot_token:
            raise ValueError("telegram.bot_token is required")
        self.config = config

    def send_message(
        self,
        chat_id: int,
        text: str,
        message_thread_id: int | None = None,
    ) -> None:
        self._run(lambda client: client.send_message(chat_id, text, message_thread_id=message_thread_id))

    def send_video(
        self,
        chat_id: int,
        path: Path,
        caption: str | None = None,
        message_thread_id: int | None = None,
    ) -> None:
        self._run(
            lambda client: client.send_video(
                chat_id,
                path,
                caption=caption,
                message_thread_id=message_thread_id,
            )
        )

    def send_photo(
        self,
        chat_id: int,
        path: Path,
        caption: str | None = None,
        message_thread_id: int | None = None,
    ) -> None:
        self._run(
            lambda client: client.send_photo(
                chat_id,
                path,
                caption=caption,
                message_thread_id=message_thread_id,
            )
        )

    def _run(self, call) -> None:
        async def runner() -> None:
            client = AsyncTelegramClient(self.config)
            try:
                await call(client)
            finally:
                await client.close()

        asyncio.run(runner())


def chat_is_allowed(config: TelegramConfig, chat_id: int) -> bool:
    if not config.allowed_chat_ids:
        return False
    return chat_id in config.allowed_chat_ids
