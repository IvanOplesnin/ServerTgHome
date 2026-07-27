from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.types import MenuButtonWebApp

from server_tg_home.core.config import Settings, TelegramPanelConfig
from server_tg_home.telegram.panels import (
    build_mini_app_markup,
    build_panel_markup,
)
from server_tg_home.telegram.polling import (
    TELEGRAM_COMMANDS,
    TelegramPolling,
    _user_is_admin,
)


class MiniAppPanelTests(unittest.TestCase):
    def test_camera_panel_gets_regular_deep_link_button(self) -> None:
        panel = TelegramPanelConfig(
            title="Гостиная",
            kind="camera",
            camera_id="living",
        )

        markup = build_panel_markup(
            "living",
            panel,
            mini_app_url="https://t.me/home_bot?startapp=group",
        )

        button = markup.inline_keyboard[-1][0]
        self.assertEqual(button.text, "Открыть умный дом")
        self.assertEqual(button.url, "https://t.me/home_bot?startapp=group")
        self.assertIsNone(button.web_app)

    def test_panel_is_unchanged_when_mini_app_is_disabled(self) -> None:
        panel = TelegramPanelConfig(
            title="Климат",
            kind="climate",
        )

        markup = build_panel_markup("climate", panel)

        self.assertEqual(len(markup.inline_keyboard), 3)
        self.assertTrue(
            all(button.url is None for row in markup.inline_keyboard for button in row)
        )

    def test_common_panel_uses_regular_url_button(self) -> None:
        markup = build_mini_app_markup(
            "https://t.me/home_bot?startapp=group"
        )

        button = markup.inline_keyboard[0][0]
        self.assertEqual(button.url, "https://t.me/home_bot?startapp=group")
        self.assertIsNone(button.web_app)


class MiniAppPollingSetupTests(unittest.IsolatedAsyncioTestCase):
    async def test_setup_configures_main_app_deep_link_and_private_menu(self) -> None:
        polling = object.__new__(TelegramPolling)
        polling.settings = Settings(
            telegram={"bot_token": "123456:test-token"},
            webapp={
                "enabled": True,
                "public_url": "https://home.example.com",
            },
        )
        polling.mini_app_launch_url = None
        bot = SimpleNamespace(
            get_me=AsyncMock(
                return_value=SimpleNamespace(username="home_bot")
            ),
            set_chat_menu_button=AsyncMock(),
        )
        polling.client = SimpleNamespace(bot=bot)

        await polling._setup_mini_app()

        self.assertEqual(
            polling.mini_app_launch_url,
            "https://t.me/home_bot?startapp=group",
        )
        call = bot.set_chat_menu_button.await_args
        menu_button = call.kwargs["menu_button"]
        self.assertIsInstance(menu_button, MenuButtonWebApp)
        self.assertEqual(menu_button.web_app.url, "https://home.example.com")

    async def test_setup_does_not_call_telegram_when_disabled(self) -> None:
        polling = object.__new__(TelegramPolling)
        polling.settings = Settings()
        polling.mini_app_launch_url = None
        bot = SimpleNamespace(
            get_me=AsyncMock(),
            set_chat_menu_button=AsyncMock(),
        )
        polling.client = SimpleNamespace(bot=bot)

        await polling._setup_mini_app()

        bot.get_me.assert_not_awaited()
        bot.set_chat_menu_button.assert_not_awaited()
        self.assertIsNone(polling.mini_app_launch_url)


class RecordingStatusCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_actions_fail_closed_without_allowlist(self) -> None:
        settings = Settings()

        self.assertFalse(_user_is_admin(settings, 42))
        settings.telegram.admin_user_ids = [42]
        self.assertTrue(_user_is_admin(settings, 42))
        self.assertFalse(_user_is_admin(settings, 99))

    async def test_recording_status_admin_guard_preserves_group_topic(self) -> None:
        polling = object.__new__(TelegramPolling)
        polling.settings = Settings(telegram={"admin_user_ids": [42]})
        polling._allowed_chat_context = AsyncMock(return_value=(-100123, 77))
        polling._reply = AsyncMock()
        denied_message = SimpleNamespace(
            from_user=SimpleNamespace(id=99),
        )
        admin_message = SimpleNamespace(
            from_user=SimpleNamespace(id=42),
        )

        denied = await polling._admin_chat_context(
            denied_message,
            "view active SSD recordings",
        )
        allowed = await polling._admin_chat_context(
            admin_message,
            "view active SSD recordings",
        )

        self.assertIsNone(denied)
        self.assertEqual(allowed, (-100123, 77))
        polling._reply.assert_awaited_once()
        self.assertEqual(
            polling._reply.await_args.kwargs["message_thread_id"],
            77,
        )

    async def test_recordings_command_uses_database_status_and_topic(self) -> None:
        polling = object.__new__(TelegramPolling)
        polling.settings = Settings(
            cameras={
                "entrance": {
                    "title": "Вход",
                    "rtsp_url": "rtsp://camera.invalid/entrance",
                }
            }
        )
        polling._reply = AsyncMock()
        session_context = MagicMock()

        with (
            patch(
                "server_tg_home.telegram.polling.new_session",
                return_value=session_context,
            ),
            patch(
                "server_tg_home.telegram.polling.list_active_recordings",
                return_value=[],
            ) as list_status,
        ):
            await polling._handle_recordings(-100123, 77, [])

        list_status.assert_called_once_with(
            session_context.__enter__.return_value,
            camera_ids={"entrance"},
        )
        polling._reply.assert_awaited_once_with(
            -100123,
            "Сейчас ни одна камера не записывается.",
            message_thread_id=77,
        )

    async def test_recordings_command_rejects_arguments_and_is_registered(self) -> None:
        polling = object.__new__(TelegramPolling)
        polling._reply = AsyncMock()

        await polling._handle_recordings(-100123, 77, ["entrance"])

        polling._reply.assert_awaited_once_with(
            -100123,
            "Usage: /recordings",
            message_thread_id=77,
        )
        self.assertIn(
            ("recordings", "Show active SSD recordings", "/recordings"),
            TELEGRAM_COMMANDS,
        )
