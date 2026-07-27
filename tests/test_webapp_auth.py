from __future__ import annotations

import hashlib
import hmac
import json
import unittest
from urllib.parse import urlencode

from server_tg_home.core.config import Settings
from server_tg_home.webapp.auth import (
    MiniAppAccessDeniedError,
    MiniAppAuthService,
    TelegramInitDataError,
    TelegramMiniAppUser,
    authorize_webapp_user,
    validate_telegram_init_data,
)
from server_tg_home.webapp.session import RedisSessionStore


BOT_TOKEN = "123456:test-token"
NOW = 2_000_000_000


def _signed_init_data(
    *,
    user_id: int = 42,
    auth_date: int = NOW,
    bot_token: str = BOT_TOKEN,
) -> str:
    fields = {
        "auth_date": str(auth_date),
        "query_id": "query-1",
        "user": json.dumps(
            {
                "id": user_id,
                "is_bot": False,
                "first_name": "Иван",
                "username": "ivan",
                "language_code": "ru",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()
    return urlencode(fields)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(
        self,
        name: str,
        value: str,
        *,
        ex: int,
        nx: bool,
    ) -> bool:
        if nx and name in self.values:
            return False
        self.values[name] = value
        self.ttls[name] = ex
        return True

    async def get(self, name: str) -> str | None:
        return self.values.get(name)

    async def delete(self, *names: str) -> int:
        deleted = 0
        for name in names:
            deleted += int(self.values.pop(name, None) is not None)
            self.ttls.pop(name, None)
        return deleted


def _settings(
    *,
    admins: list[int] | None = None,
    viewers: list[int] | None = None,
    require_membership: bool = True,
    primary_chat_id: int | None = -100123,
    enabled: bool = True,
) -> Settings:
    return Settings(
        telegram={
            "bot_token": BOT_TOKEN,
            "admin_user_ids": admins or [],
        },
        webapp={
            "enabled": enabled,
            "public_url": "https://home.example.com",
            "primary_chat_id": primary_chat_id,
            "viewer_user_ids": viewers or [],
            "require_group_membership": require_membership,
            "auth_max_age_sec": 300,
            "session_ttl_sec": 3600,
        },
    )


class TelegramInitDataTests(unittest.TestCase):
    def test_valid_init_data_is_parsed(self) -> None:
        result = validate_telegram_init_data(
            _signed_init_data(),
            BOT_TOKEN,
            300,
            now=NOW,
        )

        self.assertEqual(result.user.id, 42)
        self.assertEqual(result.user.first_name, "Иван")
        self.assertEqual(result.query_id, "query-1")

    def test_tampering_is_rejected(self) -> None:
        init_data = f"{_signed_init_data()}&start_param=changed"

        with self.assertRaises(TelegramInitDataError):
            validate_telegram_init_data(init_data, BOT_TOKEN, 300, now=NOW)

    def test_expired_init_data_is_rejected(self) -> None:
        with self.assertRaisesRegex(TelegramInitDataError, "expired"):
            validate_telegram_init_data(
                _signed_init_data(auth_date=NOW - 301),
                BOT_TOKEN,
                300,
                now=NOW,
            )

    def test_far_future_init_data_is_rejected(self) -> None:
        with self.assertRaisesRegex(TelegramInitDataError, "future"):
            validate_telegram_init_data(
                _signed_init_data(auth_date=NOW + 31),
                BOT_TOKEN,
                300,
                now=NOW,
            )

    def test_missing_bot_token_is_rejected(self) -> None:
        with self.assertRaises(TelegramInitDataError):
            validate_telegram_init_data(_signed_init_data(), None, 300, now=NOW)

    def test_duplicate_fields_are_rejected(self) -> None:
        init_data = _signed_init_data()
        duplicate_hash = init_data.rsplit("hash=", maxsplit=1)[1]

        with self.assertRaisesRegex(TelegramInitDataError, "duplicate"):
            validate_telegram_init_data(
                f"{init_data}&hash={duplicate_hash}",
                BOT_TOKEN,
                300,
                now=NOW,
            )


class AccessPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_admin_does_not_require_group_membership(self) -> None:
        calls: list[tuple[int, int]] = []

        async def membership(chat_id: int, user_id: int) -> bool:
            calls.append((chat_id, user_id))
            return False

        principal = await authorize_webapp_user(
            _settings(admins=[42]),
            TelegramMiniAppUser(id=42, first_name="Admin"),
            membership,
        )

        self.assertEqual(principal.role, "admin")
        self.assertEqual(calls, [])

    async def test_viewer_must_be_in_primary_group(self) -> None:
        calls: list[tuple[int, int]] = []

        async def membership(chat_id: int, user_id: int) -> bool:
            calls.append((chat_id, user_id))
            return True

        principal = await authorize_webapp_user(
            _settings(viewers=[42]),
            TelegramMiniAppUser(id=42, first_name="Viewer"),
            membership,
        )

        self.assertEqual(principal.role, "viewer")
        self.assertEqual(calls, [(-100123, 42)])

    async def test_empty_allowlists_deny_access(self) -> None:
        with self.assertRaises(MiniAppAccessDeniedError):
            await authorize_webapp_user(
                _settings(),
                TelegramMiniAppUser(id=42, first_name="Unknown"),
                None,
            )

    async def test_missing_membership_checker_denies_viewer(self) -> None:
        with self.assertRaisesRegex(MiniAppAccessDeniedError, "cannot be verified"):
            await authorize_webapp_user(
                _settings(viewers=[42]),
                TelegramMiniAppUser(id=42, first_name="Viewer"),
                None,
            )

    async def test_membership_can_be_disabled_for_allowlisted_viewer(self) -> None:
        principal = await authorize_webapp_user(
            _settings(viewers=[42], require_membership=False),
            TelegramMiniAppUser(id=42, first_name="Viewer"),
            None,
        )

        self.assertEqual(principal.role, "viewer")

    async def test_membership_error_fails_closed(self) -> None:
        async def broken_membership(chat_id: int, user_id: int) -> bool:
            raise RuntimeError("network is down")

        with self.assertRaisesRegex(MiniAppAccessDeniedError, "not a member"):
            await authorize_webapp_user(
                _settings(viewers=[42]),
                TelegramMiniAppUser(id=42, first_name="Viewer"),
                broken_membership,
            )


class AuthServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_creates_and_authenticates_viewer_session(self) -> None:
        current_time = [NOW]
        redis = FakeRedis()
        store = RedisSessionStore(redis, 3600, clock=lambda: current_time[0])

        async def membership(chat_id: int, user_id: int) -> bool:
            return True

        auth = MiniAppAuthService(
            _settings(viewers=[42]),
            store,
            membership,
            clock=lambda: current_time[0],
        )

        created = await auth.login(_signed_init_data())
        session = await auth.authenticate(created.token)

        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session.user_id, 42)
        self.assertEqual(session.role, "viewer")
        self.assertNotIn("token", created.model_dump())

    async def test_disabled_webapp_rejects_login_and_session(self) -> None:
        redis = FakeRedis()
        store = RedisSessionStore(redis, 3600, clock=lambda: NOW)
        auth = MiniAppAuthService(
            _settings(admins=[42], enabled=False),
            store,
            clock=lambda: NOW,
        )

        with self.assertRaisesRegex(MiniAppAccessDeniedError, "disabled"):
            await auth.login(_signed_init_data())
        self.assertIsNone(await auth.authenticate("x" * 40))

    async def test_removed_user_loses_existing_session(self) -> None:
        settings = _settings(admins=[42])
        redis = FakeRedis()
        store = RedisSessionStore(redis, 3600, clock=lambda: NOW)
        auth = MiniAppAuthService(settings, store, clock=lambda: NOW)
        created = await auth.login(_signed_init_data())

        settings.telegram.admin_user_ids.clear()

        self.assertIsNone(await auth.authenticate(created.token))
        self.assertIsNone(await store.get(created.token))
