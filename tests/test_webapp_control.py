from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from server_tg_home.core.config import Settings
from server_tg_home.database.models import Job, Video
from server_tg_home.database.session import Base
from server_tg_home.webapp.auth import MiniAppAuthService
from server_tg_home.webapp.control import create_webapp_control_router
from server_tg_home.webapp.dependencies import (
    get_webapp_session,
    require_webapp_session,
)
from server_tg_home.webapp.router import create_webapp_router
from server_tg_home.webapp.session import RedisSessionStore
from server_tg_home.webapp.tickets import RedisTicketStore

from tests.test_webapp_auth import BOT_TOKEN, NOW, FakeRedis, _signed_init_data


class WebAppControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.storage_path = Path(self.temp_dir.name) / "clips"
        self.buffer_path = Path(self.temp_dir.name) / "buffer"
        self.storage_path.mkdir()
        self.buffer_path.mkdir()
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(
            self.engine,
            expire_on_commit=False,
        )
        self.settings = Settings(
            telegram={
                "bot_token": BOT_TOKEN,
                "admin_user_ids": [42],
            },
            webapp={
                "enabled": True,
                "public_url": "https://home.example.com/app",
                "primary_chat_id": -100123,
                "session_ttl_sec": 3600,
                "media_ticket_ttl_sec": 600,
                "video_ticket_ttl_sec": 120,
                "tabs": [
                    {
                        "id": "cameras",
                        "title": "Камеры",
                        "kind": "cameras",
                        "required_role": "viewer",
                    },
                    {
                        "id": "admin",
                        "title": "Администрирование",
                        "kind": "admin",
                        "required_role": "admin",
                    },
                ],
            },
            storage={"path": self.storage_path},
            buffer={"path": self.buffer_path},
            cameras={
                "entrance": {
                    "title": "Вход",
                    "web_enabled": True,
                    "rtsp_url": "rtsp://camera.invalid/entrance",
                    "go2rtc_stream": "entrance-live",
                },
                "private": {
                    "title": "Служебная",
                    "web_enabled": False,
                    "rtsp_url": "rtsp://camera.invalid/private",
                    "go2rtc_stream": "private-live",
                },
            },
            temperatures={
                "rooms": {
                    "bedroom": {"title": "Спальня"},
                }
            },
        )
        redis = FakeRedis()
        self.session_store = RedisSessionStore(
            redis,
            self.settings.webapp.session_ttl_sec,
            clock=lambda: NOW,
        )
        self.ticket_store = RedisTicketStore(redis, clock=lambda: NOW)
        self.auth = MiniAppAuthService(
            self.settings,
            self.session_store,
            clock=lambda: NOW,
        )

        self.app = FastAPI()
        self.app.state.settings = self.settings
        self.app.state.webapp_auth = self.auth
        self.app.state.webapp_tickets = self.ticket_store
        self.app.include_router(create_webapp_control_router())
        self.app.include_router(
            create_webapp_router(auth_dependency=require_webapp_session)
        )
        self.app.dependency_overrides[get_webapp_session] = self._db_session
        self.client = TestClient(
            self.app,
            base_url="https://home.example.com",
        )

    def tearDown(self) -> None:
        self.client.close()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def _db_session(self):
        session = self.sessions()
        try:
            yield session
        finally:
            session.close()

    def _login(self) -> str:
        response = self.client.post(
            "/api/webapp/v1/session",
            json={"init_data": _signed_init_data()},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return str(response.json()["access_token"])

    def _add_video(self) -> tuple[int, bytes]:
        data = b"0123456789"
        path = self.storage_path / "entrance" / "archive.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        with self.sessions() as session:
            session.add(
                Job(
                    id="job-1",
                    type="record_video_file",
                    source="test",
                    status="done",
                    payload={},
                )
            )
            video = Video(
                job_id="job-1",
                camera_id="entrance",
                path=str(path),
                size_bytes=len(data),
                duration_sec=10,
                created_at=datetime.now(UTC),
            )
            session.add(video)
            session.commit()
            return video.id, data

    def test_session_cookie_bearer_and_bootstrap_contract(self) -> None:
        token = self._login()
        set_cookie = self.client.post(
            "/api/webapp/v1/session",
            json={"init_data": _signed_init_data()},
        ).headers["set-cookie"]

        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("Secure", set_cookie)
        self.assertIn("SameSite=none", set_cookie)
        self.assertIn("Path=/api/webapp/v1", set_cookie)

        self.client.cookies.clear()
        response = self.client.get(
            "/api/webapp/v1/bootstrap",
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["user"]["id"], 42)
        self.assertEqual(payload["user"]["role"], "admin")
        self.assertTrue(payload["user"]["is_admin"])
        self.assertEqual(
            [camera["id"] for camera in payload["cameras"]],
            ["entrance"],
        )
        self.assertEqual(
            [tab["id"] for tab in payload["tabs"]],
            ["cameras", "admin"],
        )
        self.assertEqual(
            payload["climate_rooms"],
            [{"id": "bedroom", "title": "Спальня"}],
        )

    def test_read_api_rejects_missing_or_malformed_session(self) -> None:
        missing = self.client.get("/api/webapp/v1/cameras")
        malformed = self.client.get(
            "/api/webapp/v1/cameras",
            headers={"Authorization": "Basic something"},
        )

        self.assertEqual(missing.status_code, 401)
        self.assertEqual(malformed.status_code, 401)
        self.assertEqual(missing.headers["www-authenticate"], "Bearer")

    def test_stream_ticket_is_scoped_to_camera_and_media_routes(self) -> None:
        token = self._login()
        self.client.cookies.clear()
        response = self.client.post(
            "/api/webapp/v1/streams/entrance/ticket",
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        ws = urlsplit(payload["ws_url"])
        self.assertEqual(ws.scheme, "wss")
        self.assertEqual(ws.netloc, "home.example.com")
        self.assertIn("/media/t/", ws.path)
        self.assertEqual(ws.query, "src=entrance-live")
        self.assertEqual(
            payload["player_script_url"],
            "/media/video-stream.js",
        )

        allowed = self.client.get(
            "/api/webapp/v1/media/authorize",
            headers={
                "X-Forwarded-Method": "GET",
                "X-Forwarded-Uri": f"{ws.path}?{ws.query}",
            },
        )
        wrong_source = self.client.get(
            "/api/webapp/v1/media/authorize",
            headers={
                "X-Forwarded-Method": "GET",
                "X-Forwarded-Uri": f"{ws.path}?src=private-live",
            },
        )
        broad_api = self.client.get(
            "/api/webapp/v1/media/authorize",
            headers={
                "X-Forwarded-Method": "GET",
                "X-Forwarded-Uri": ws.path.replace("/ws", "/streams"),
            },
        )
        hls_segment = self.client.get(
            "/api/webapp/v1/media/authorize",
            headers={
                "X-Forwarded-Method": "GET",
                "X-Forwarded-Uri": (
                    ws.path.replace("/ws", "/hls/segment.m4s")
                    + "?id=opaque-go2rtc-session&n=1"
                ),
            },
        )

        self.assertEqual(allowed.status_code, 204)
        self.assertEqual(hls_segment.status_code, 204)
        self.assertEqual(wrong_source.status_code, 401)
        self.assertEqual(broad_api.status_code, 401)

    def test_disabled_camera_cannot_get_stream_ticket(self) -> None:
        self._login()

        response = self.client.post(
            "/api/webapp/v1/streams/private/ticket",
            headers={"X-STH-WebApp": "1"},
        )

        self.assertEqual(response.status_code, 404)

    def test_video_ticket_supports_unauthenticated_range_and_download(self) -> None:
        video_id, data = self._add_video()
        token = self._login()
        self.client.cookies.clear()
        ticket_response = self.client.post(
            f"/api/webapp/v1/videos/{video_id}/download-ticket",
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(ticket_response.status_code, 200, ticket_response.text)
        payload = ticket_response.json()

        content = self.client.get(
            payload["content_url"],
            headers={"Range": "bytes=2-5"},
        )
        download = self.client.get(payload["url"])

        self.assertEqual(content.status_code, 206)
        self.assertEqual(content.content, data[2:6])
        self.assertIn("inline", content.headers["content-disposition"])
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.content, data)
        self.assertIn("attachment", download.headers["content-disposition"])
        self.assertIn("filename=", download.headers["content-disposition"])

    def test_revoked_user_loses_existing_capability_ticket(self) -> None:
        video_id, _ = self._add_video()
        self._login()
        ticket = self.client.post(
            f"/api/webapp/v1/videos/{video_id}/download-ticket",
            headers={"X-STH-WebApp": "1"},
        ).json()

        self.settings.telegram.admin_user_ids.clear()
        response = self.client.get(ticket["content_url"])

        self.assertEqual(response.status_code, 404)

    def test_logout_revokes_session_and_clears_cookie(self) -> None:
        self._login()

        logout = self.client.delete(
            "/api/webapp/v1/session",
            headers={"X-STH-WebApp": "1"},
        )
        after = self.client.get("/api/webapp/v1/bootstrap")

        self.assertEqual(logout.status_code, 204)
        self.assertIn("sth_webapp_session=", logout.headers["set-cookie"])
        self.assertEqual(after.status_code, 401)

    def test_cookie_authenticated_post_requires_csrf_marker(self) -> None:
        self._login()

        rejected = self.client.post(
            "/api/webapp/v1/streams/entrance/ticket"
        )
        accepted = self.client.post(
            "/api/webapp/v1/streams/entrance/ticket",
            headers={"X-STH-WebApp": "1"},
        )

        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(
            rejected.json()["detail"]["code"],
            "csrf_check_failed",
        )
        self.assertEqual(accepted.status_code, 200)


if __name__ == "__main__":
    unittest.main()
