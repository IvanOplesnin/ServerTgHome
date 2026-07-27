from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Callable
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from redis.asyncio import Redis


SESSION_KEY_PREFIX = "server_tg_home:webapp:session:"
MAX_SESSION_TOKEN_LENGTH = 256


class AsyncSessionRedis(Protocol):
    async def set(
        self,
        name: str,
        value: str,
        *,
        ex: int,
        nx: bool,
    ) -> Any: ...

    async def get(self, name: str) -> str | bytes | None: ...

    async def delete(self, *names: str) -> Any: ...


class SessionPrincipal(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: int = Field(gt=0)
    role: Literal["admin", "viewer"]
    first_name: str
    last_name: str | None = None
    username: str | None = None
    language_code: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


class SessionRecord(SessionPrincipal):
    created_at: int = Field(ge=0)
    expires_at: int = Field(gt=0)

    @model_validator(mode="after")
    def require_expiration_after_creation(self) -> SessionRecord:
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at")
        return self


class CreatedSession(BaseModel):
    model_config = ConfigDict(frozen=True)

    token: str = Field(
        min_length=32,
        max_length=MAX_SESSION_TOKEN_LENGTH,
        exclude=True,
        repr=False,
    )
    session: SessionRecord


def _unix_time() -> int:
    return int(time.time())


class RedisSessionStore:
    """Stores opaque Mini App sessions in Redis using hashed lookup keys."""

    def __init__(
        self,
        redis: AsyncSessionRedis,
        ttl_sec: int,
        *,
        key_prefix: str = SESSION_KEY_PREFIX,
        clock: Callable[[], int] | None = None,
        owns_client: bool = False,
    ) -> None:
        if ttl_sec < 1:
            raise ValueError("ttl_sec must be positive")
        self._redis = redis
        self._ttl_sec = ttl_sec
        self._key_prefix = key_prefix
        self._clock = clock or _unix_time
        self._owns_client = owns_client

    @classmethod
    def from_url(
        cls,
        redis_url: str,
        ttl_sec: int,
        *,
        key_prefix: str = SESSION_KEY_PREFIX,
        clock: Callable[[], int] | None = None,
    ) -> Self:
        client = Redis.from_url(redis_url, decode_responses=True)
        return cls(
            client,
            ttl_sec,
            key_prefix=key_prefix,
            clock=clock,
            owns_client=True,
        )

    async def create(self, principal: SessionPrincipal) -> CreatedSession:
        created_at = self._clock()
        record = SessionRecord(
            **principal.model_dump(),
            created_at=created_at,
            expires_at=created_at + self._ttl_sec,
        )
        payload = record.model_dump_json()

        for _ in range(3):
            token = secrets.token_urlsafe(32)
            stored = await self._redis.set(
                self._session_key(token),
                payload,
                ex=self._ttl_sec,
                nx=True,
            )
            if stored:
                return CreatedSession(token=token, session=record)
        raise RuntimeError("Could not allocate a unique Mini App session")

    async def get(self, token: str | None) -> SessionRecord | None:
        if not self._valid_token(token):
            return None
        key = self._session_key(token)
        payload = await self._redis.get(key)
        if payload is None:
            return None
        try:
            record = SessionRecord.model_validate_json(payload)
        except (ValidationError, ValueError, TypeError):
            await self._redis.delete(key)
            return None
        if record.expires_at <= self._clock():
            await self._redis.delete(key)
            return None
        return record

    async def delete(self, token: str | None) -> None:
        if self._valid_token(token):
            await self._redis.delete(self._session_key(token))

    async def close(self) -> None:
        if self._owns_client:
            await self._redis.aclose()  # type: ignore[attr-defined]

    def _session_key(self, token: str) -> str:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return f"{self._key_prefix}{token_hash}"

    @staticmethod
    def _valid_token(token: str | None) -> bool:
        return (
            isinstance(token, str)
            and 32 <= len(token) <= MAX_SESSION_TOKEN_LENGTH
        )
