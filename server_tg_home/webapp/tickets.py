from __future__ import annotations

import hashlib
import re
import secrets
import time
from collections.abc import Callable
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from redis.asyncio import Redis


TICKET_KEY_PREFIX = "server_tg_home:webapp:ticket:"
MIN_TICKET_TOKEN_LENGTH = 32
MAX_TICKET_TOKEN_LENGTH = 256
MAX_TICKET_RESOURCE_ID_LENGTH = 512
MAX_TICKET_TTL_SEC = 3_600

TicketPurpose = Literal["stream", "download"]
_TOKEN_PATTERN = re.compile(
    rf"[A-Za-z0-9_-]{{{MIN_TICKET_TOKEN_LENGTH},{MAX_TICKET_TOKEN_LENGTH}}}"
)


class AsyncTicketRedis(Protocol):
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


class TicketPrincipal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    purpose: TicketPurpose
    user_id: int = Field(gt=0)
    resource_id: str = Field(
        min_length=1,
        max_length=MAX_TICKET_RESOURCE_ID_LENGTH,
    )


class CapabilityTicket(TicketPrincipal):
    """Server-side payload associated with an opaque capability token."""

    created_at: int = Field(ge=0)
    expires_at: int = Field(gt=0)

    @model_validator(mode="after")
    def require_expiration_after_creation(self) -> CapabilityTicket:
        if self.expires_at <= self.created_at:
            raise ValueError("expires_at must be later than created_at")
        return self


class CreatedCapabilityTicket(BaseModel):
    """A newly allocated token and its non-secret server-side payload."""

    model_config = ConfigDict(frozen=True)

    token: str = Field(
        min_length=MIN_TICKET_TOKEN_LENGTH,
        max_length=MAX_TICKET_TOKEN_LENGTH,
        exclude=True,
        repr=False,
    )
    ticket: CapabilityTicket


def _unix_time() -> int:
    return int(time.time())


class RedisTicketStore:
    """Stores short-lived opaque capability tickets under hashed Redis keys."""

    def __init__(
        self,
        redis: AsyncTicketRedis,
        *,
        key_prefix: str = TICKET_KEY_PREFIX,
        clock: Callable[[], int] | None = None,
        owns_client: bool = False,
    ) -> None:
        self._redis = redis
        self._key_prefix = key_prefix
        self._clock = clock or _unix_time
        self._owns_client = owns_client

    @classmethod
    def from_url(
        cls,
        redis_url: str,
        *,
        key_prefix: str = TICKET_KEY_PREFIX,
        clock: Callable[[], int] | None = None,
    ) -> Self:
        client = Redis.from_url(redis_url, decode_responses=True)
        return cls(
            client,
            key_prefix=key_prefix,
            clock=clock,
            owns_client=True,
        )

    async def create(
        self,
        principal: TicketPrincipal,
        *,
        ttl_sec: int,
    ) -> CreatedCapabilityTicket:
        if not 1 <= ttl_sec <= MAX_TICKET_TTL_SEC:
            raise ValueError(
                f"ttl_sec must be between 1 and {MAX_TICKET_TTL_SEC}"
            )
        created_at = self._clock()
        ticket = CapabilityTicket(
            **principal.model_dump(),
            created_at=created_at,
            expires_at=created_at + ttl_sec,
        )
        payload = ticket.model_dump_json()

        for _ in range(3):
            token = secrets.token_urlsafe(32)
            stored = await self._redis.set(
                self._ticket_key(token),
                payload,
                ex=ttl_sec,
                nx=True,
            )
            if stored:
                return CreatedCapabilityTicket(token=token, ticket=ticket)
        raise RuntimeError("Could not allocate a unique Mini App capability ticket")

    async def get(
        self,
        token: str | None,
        *,
        purpose: TicketPurpose,
    ) -> CapabilityTicket | None:
        if not self._valid_token(token):
            return None
        key = self._ticket_key(token)
        payload = await self._redis.get(key)
        if payload is None:
            return None
        try:
            ticket = CapabilityTicket.model_validate_json(payload)
        except (ValidationError, ValueError, TypeError):
            await self._redis.delete(key)
            return None
        if ticket.expires_at <= self._clock():
            await self._redis.delete(key)
            return None
        if ticket.purpose != purpose:
            return None
        return ticket

    async def delete(self, token: str | None) -> None:
        if self._valid_token(token):
            await self._redis.delete(self._ticket_key(token))

    async def close(self) -> None:
        if self._owns_client:
            await self._redis.aclose()  # type: ignore[attr-defined]

    def _ticket_key(self, token: str) -> str:
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        return f"{self._key_prefix}{token_hash}"

    @staticmethod
    def _valid_token(token: str | None) -> bool:
        return isinstance(token, str) and _TOKEN_PATTERN.fullmatch(token) is not None
