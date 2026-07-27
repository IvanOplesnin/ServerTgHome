from __future__ import annotations

import unittest

from server_tg_home.webapp.session import RedisSessionStore, SessionPrincipal

from tests.test_webapp_auth import FakeRedis


class RedisSessionStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_uses_hashed_key_and_redis_ttl(self) -> None:
        current_time = [1000]
        redis = FakeRedis()
        store = RedisSessionStore(redis, 600, clock=lambda: current_time[0])
        principal = SessionPrincipal(
            user_id=42,
            role="viewer",
            first_name="Иван",
        )

        created = await store.create(principal)

        self.assertGreaterEqual(len(created.token), 32)
        self.assertTrue(all(created.token not in key for key in redis.values))
        self.assertEqual(list(redis.ttls.values()), [600])
        self.assertEqual((await store.get(created.token)).user_id, 42)  # type: ignore[union-attr]

    async def test_expired_session_is_deleted(self) -> None:
        current_time = [1000]
        redis = FakeRedis()
        store = RedisSessionStore(redis, 60, clock=lambda: current_time[0])
        created = await store.create(
            SessionPrincipal(user_id=42, role="admin", first_name="Admin")
        )

        current_time[0] = 1060

        self.assertIsNone(await store.get(created.token))
        self.assertEqual(redis.values, {})

    async def test_logout_deletes_session(self) -> None:
        redis = FakeRedis()
        store = RedisSessionStore(redis, 60, clock=lambda: 1000)
        created = await store.create(
            SessionPrincipal(user_id=42, role="admin", first_name="Admin")
        )

        await store.delete(created.token)

        self.assertIsNone(await store.get(created.token))

    async def test_malformed_redis_payload_fails_closed(self) -> None:
        redis = FakeRedis()
        store = RedisSessionStore(redis, 60, clock=lambda: 1000)
        token = "x" * 40
        key = store._session_key(token)
        redis.values[key] = "not-json"

        self.assertIsNone(await store.get(token))
        self.assertNotIn(key, redis.values)
