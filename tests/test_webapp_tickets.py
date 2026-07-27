from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from server_tg_home.webapp.tickets import (
    MAX_TICKET_TTL_SEC,
    RedisTicketStore,
    TicketPrincipal,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str | bytes] = {}
        self.ttls: dict[str, int] = {}
        self.get_calls: list[str] = []
        self.delete_calls: list[str] = []
        self.closed = False

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

    async def get(self, name: str) -> str | bytes | None:
        self.get_calls.append(name)
        return self.values.get(name)

    async def delete(self, *names: str) -> int:
        deleted = 0
        for name in names:
            self.delete_calls.append(name)
            if name in self.values:
                deleted += 1
                del self.values[name]
            self.ttls.pop(name, None)
        return deleted

    async def aclose(self) -> None:
        self.closed = True


class RedisTicketStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_ticket_uses_hashed_key_ttl_and_excludes_token(self) -> None:
        redis = FakeRedis()
        store = RedisTicketStore(redis, clock=lambda: 1000)

        created = await store.create(
            TicketPrincipal(
                purpose="stream",
                user_id=42,
                resource_id="entrance",
            ),
            ttl_sec=90,
        )

        self.assertGreaterEqual(len(created.token), 32)
        self.assertTrue(all(created.token not in key for key in redis.values))
        self.assertTrue(
            all(created.token not in str(payload) for payload in redis.values.values())
        )
        self.assertEqual(list(redis.ttls.values()), [90])
        self.assertNotIn("token", created.model_dump())
        self.assertNotIn(created.token, repr(created))

        ticket = await store.get(created.token, purpose="stream")

        self.assertIsNotNone(ticket)
        self.assertEqual(ticket.user_id, 42)  # type: ignore[union-attr]
        self.assertEqual(ticket.resource_id, "entrance")  # type: ignore[union-attr]
        self.assertEqual(ticket.created_at, 1000)  # type: ignore[union-attr]
        self.assertEqual(ticket.expires_at, 1090)  # type: ignore[union-attr]

    async def test_expired_ticket_is_deleted(self) -> None:
        current_time = [1000]
        redis = FakeRedis()
        store = RedisTicketStore(
            redis,
            clock=lambda: current_time[0],
        )
        created = await store.create(
            TicketPrincipal(
                purpose="download",
                user_id=42,
                resource_id="123",
            ),
            ttl_sec=60,
        )

        current_time[0] = 1060

        self.assertIsNone(await store.get(created.token, purpose="download"))
        self.assertEqual(redis.values, {})

    async def test_wrong_purpose_fails_closed_without_destroying_ticket(self) -> None:
        redis = FakeRedis()
        store = RedisTicketStore(redis, clock=lambda: 1000)
        created = await store.create(
            TicketPrincipal(
                purpose="stream",
                user_id=42,
                resource_id="living",
            ),
            ttl_sec=60,
        )

        self.assertIsNone(await store.get(created.token, purpose="download"))
        self.assertIsNotNone(await store.get(created.token, purpose="stream"))

    async def test_malformed_payload_is_deleted(self) -> None:
        redis = FakeRedis()
        store = RedisTicketStore(redis, clock=lambda: 1000)
        token = "x" * 40
        key = store._ticket_key(token)

        malformed_payloads: list[Any] = [
            "not-json",
            '{"purpose":"other","user_id":42,"resource_id":"entrance",'
            '"created_at":1000,"expires_at":1060}',
            '{"purpose":"stream","user_id":42,"resource_id":"entrance",'
            '"created_at":1000,"expires_at":999}',
        ]
        for payload in malformed_payloads:
            with self.subTest(payload=payload):
                redis.values[key] = payload

                self.assertIsNone(await store.get(token, purpose="stream"))
                self.assertNotIn(key, redis.values)

    async def test_invalid_token_is_rejected_without_redis_lookup(self) -> None:
        redis = FakeRedis()
        store = RedisTicketStore(redis, clock=lambda: 1000)

        for token in (None, "", "short", "x y" * 10, "я" * 40, "x" * 257):
            with self.subTest(token=token):
                self.assertIsNone(await store.get(token, purpose="stream"))
                await store.delete(token)

        self.assertEqual(redis.get_calls, [])
        self.assertEqual(redis.delete_calls, [])

    async def test_delete_removes_ticket(self) -> None:
        redis = FakeRedis()
        store = RedisTicketStore(redis, clock=lambda: 1000)
        created = await store.create(
            TicketPrincipal(
                purpose="download",
                user_id=42,
                resource_id="123",
            ),
            ttl_sec=60,
        )

        await store.delete(created.token)

        self.assertIsNone(await store.get(created.token, purpose="download"))

    async def test_from_url_owns_and_closes_redis_client(self) -> None:
        redis = FakeRedis()
        with patch(
            "server_tg_home.webapp.tickets.Redis.from_url",
            return_value=redis,
        ) as from_url:
            store = RedisTicketStore.from_url(
                "redis://redis:6379/0",
            )

        from_url.assert_called_once_with(
            "redis://redis:6379/0",
            decode_responses=True,
        )
        await store.close()
        self.assertTrue(redis.closed)

    async def test_ttl_is_bounded(self) -> None:
        store = RedisTicketStore(FakeRedis(), clock=lambda: 1000)
        principal = TicketPrincipal(
            purpose="stream",
            user_id=42,
            resource_id="entrance",
        )

        for ttl_sec in (0, -1, MAX_TICKET_TTL_SEC + 1):
            with self.subTest(ttl_sec=ttl_sec):
                with self.assertRaises(ValueError):
                    await store.create(principal, ttl_sec=ttl_sec)


if __name__ == "__main__":
    unittest.main()
