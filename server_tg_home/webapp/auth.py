from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from collections.abc import Callable
from typing import Any, Protocol
from urllib.parse import parse_qsl

from aiogram import Bot
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from server_tg_home.core.config import Settings
from server_tg_home.webapp.session import (
    CreatedSession,
    RedisSessionStore,
    SessionPrincipal,
    SessionRecord,
)

logger = logging.getLogger(__name__)

MAX_INIT_DATA_LENGTH = 16_384
MAX_FUTURE_SKEW_SEC = 30


class MiniAppAuthError(ValueError):
    """Base error for a rejected Mini App authentication attempt."""


class TelegramInitDataError(MiniAppAuthError):
    """Telegram initData is absent, malformed, invalid, or expired."""


class MiniAppAccessDeniedError(MiniAppAuthError):
    """The authenticated Telegram user is not allowed to use the Mini App."""


class TelegramMiniAppUser(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    id: int = Field(gt=0)
    is_bot: bool = False
    first_name: str = Field(min_length=1)
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None


class ValidatedInitData(BaseModel):
    model_config = ConfigDict(frozen=True)

    user: TelegramMiniAppUser
    auth_date: int = Field(gt=0)
    query_id: str | None = None


class MembershipChecker(Protocol):
    async def __call__(self, chat_id: int, user_id: int) -> bool: ...


def _unix_time() -> int:
    return int(time.time())


def validate_telegram_init_data(
    init_data: str,
    bot_token: str | None,
    max_age_sec: int,
    *,
    now: int | None = None,
) -> ValidatedInitData:
    """Validate Telegram Mini App initData using Telegram's HMAC procedure."""

    if not bot_token:
        raise TelegramInitDataError("Telegram bot token is not configured")
    if not init_data or len(init_data) > MAX_INIT_DATA_LENGTH:
        raise TelegramInitDataError("Telegram initData is missing or too large")
    if max_age_sec < 1:
        raise ValueError("max_age_sec must be positive")

    try:
        pairs = parse_qsl(
            init_data,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=100,
        )
    except ValueError as exc:
        raise TelegramInitDataError("Telegram initData is malformed") from exc

    fields: dict[str, str] = {}
    for key, value in pairs:
        if not key or key in fields:
            raise TelegramInitDataError("Telegram initData contains duplicate or empty fields")
        fields[key] = value

    received_hash = fields.pop("hash", None)
    if received_hash is None or len(received_hash) != 64:
        raise TelegramInitDataError("Telegram initData hash is missing or malformed")
    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret_key = hmac.new(
        b"WebAppData",
        bot_token.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_hash, received_hash.lower()):
        raise TelegramInitDataError("Telegram initData signature is invalid")

    try:
        auth_date = int(fields["auth_date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TelegramInitDataError("Telegram initData auth_date is invalid") from exc

    current_time = _unix_time() if now is None else now
    if auth_date > current_time + MAX_FUTURE_SKEW_SEC:
        raise TelegramInitDataError("Telegram initData auth_date is in the future")
    if current_time - auth_date > max_age_sec:
        raise TelegramInitDataError("Telegram initData has expired")

    try:
        raw_user: Any = json.loads(fields["user"])
        user = TelegramMiniAppUser.model_validate(raw_user)
    except (KeyError, json.JSONDecodeError, TypeError, ValidationError) as exc:
        raise TelegramInitDataError("Telegram initData user is invalid") from exc
    if user.is_bot:
        raise TelegramInitDataError("Telegram bot accounts cannot use the Mini App")

    return ValidatedInitData(
        user=user,
        auth_date=auth_date,
        query_id=fields.get("query_id"),
    )


class AiogramMembershipChecker:
    """Fail-closed Telegram group membership check backed by an aiogram Bot."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot

    async def __call__(self, chat_id: int, user_id: int) -> bool:
        try:
            member = await self._bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        except TelegramAPIError:
            logger.warning("Could not verify Telegram group membership")
            return False

        status = member.status
        if status in {
            ChatMemberStatus.CREATOR,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.MEMBER,
        }:
            return True
        if status == ChatMemberStatus.RESTRICTED:
            return bool(getattr(member, "is_member", False))
        return False


async def authorize_webapp_user(
    settings: Settings,
    user: TelegramMiniAppUser,
    membership_checker: MembershipChecker | None,
) -> SessionPrincipal:
    user_id = user.id
    if user_id in settings.telegram.admin_user_ids:
        role = "admin"
    elif user_id in settings.webapp.viewer_user_ids:
        role = "viewer"
    else:
        raise MiniAppAccessDeniedError("Telegram user is not in a Mini App allowlist")

    if role == "viewer" and settings.webapp.require_group_membership:
        chat_id = settings.webapp.primary_chat_id
        if chat_id is None or membership_checker is None:
            raise MiniAppAccessDeniedError("Telegram group membership cannot be verified")
        try:
            is_member = await membership_checker(chat_id, user_id)
        except Exception:
            logger.warning("Telegram group membership check failed")
            is_member = False
        if not is_member:
            raise MiniAppAccessDeniedError("Telegram user is not a member of the primary group")

    return SessionPrincipal(
        user_id=user_id,
        role=role,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
        language_code=user.language_code,
    )


class MiniAppAuthService:
    def __init__(
        self,
        settings: Settings,
        session_store: RedisSessionStore,
        membership_checker: MembershipChecker | None = None,
        *,
        clock: Callable[[], int] | None = None,
    ) -> None:
        self._settings = settings
        self._session_store = session_store
        self._membership_checker = membership_checker
        self._clock = clock or _unix_time

    async def login(self, init_data: str) -> CreatedSession:
        if not self._settings.webapp.enabled:
            raise MiniAppAccessDeniedError("Telegram Mini App is disabled")
        validated = validate_telegram_init_data(
            init_data,
            self._settings.telegram.bot_token,
            self._settings.webapp.auth_max_age_sec,
            now=self._clock(),
        )
        principal = await authorize_webapp_user(
            self._settings,
            validated.user,
            self._membership_checker,
        )
        return await self._session_store.create(principal)

    async def authenticate(self, token: str | None) -> SessionRecord | None:
        if not self._settings.webapp.enabled:
            return None
        session = await self._session_store.get(token)
        if session is None:
            return None

        current_ids = (
            self._settings.telegram.admin_user_ids
            if session.role == "admin"
            else self._settings.webapp.viewer_user_ids
        )
        if session.user_id not in current_ids:
            await self._session_store.delete(token)
            return None
        return session

    async def logout(self, token: str | None) -> None:
        await self._session_store.delete(token)
