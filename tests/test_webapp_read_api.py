from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from server_tg_home.core.config import Settings
from server_tg_home.core.temperatures import update_humidity, update_temperature
from server_tg_home.database.models import AppState, Job, SensorReading, Video
from server_tg_home.database.session import Base
from server_tg_home.webapp.climate import (
    InvalidClimateHistory,
    UnknownClimateRoom,
    get_climate_history,
    get_current_climate,
)
from server_tg_home.webapp.router import create_webapp_router
from server_tg_home.webapp.videos import (
    InvalidVideoCursor,
    VideoFileUnavailable,
    VideoNotFound,
    VideoRepository,
)


class WebAppReadTestCase(unittest.TestCase):
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
            storage={"path": self.storage_path},
            buffer={"path": self.buffer_path},
            cameras={
                "entrance": {
                    "title": "Вход",
                    "web_enabled": True,
                    "rtsp_url": "rtsp://camera.invalid/entrance",
                    "go2rtc_stream": "entrance",
                },
                "private": {
                    "title": "Служебная",
                    "web_enabled": False,
                    "rtsp_url": "rtsp://camera.invalid/private",
                },
            },
            temperatures={
                "stale_after_sec": 120,
                "rooms": {
                    "bedroom": {"title": "Спальня"},
                },
            },
        )

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp_dir.cleanup()

    def add_job(self, session: Session, job_id: str = "job-1") -> None:
        session.add(
            Job(
                id=job_id,
                type="record_video_file",
                source="test",
                status="done",
                payload={},
            )
        )

    def add_video(
        self,
        session: Session,
        *,
        path: Path,
        camera_id: str = "entrance",
        created_at: datetime,
        deleted_at: datetime | None = None,
    ) -> Video:
        video = Video(
            job_id="job-1",
            camera_id=camera_id,
            path=str(path),
            size_bytes=path.stat().st_size if path.exists() else 0,
            duration_sec=15,
            deleted_at=deleted_at,
            created_at=created_at,
        )
        session.add(video)
        session.flush()
        return video

    def make_video_file(self, name: str, data: bytes = b"0123456789") -> Path:
        path = self.storage_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def make_api_client(self) -> TestClient:
        async def allow_test_user() -> object:
            return object()

        def test_session():
            session = self.sessions()
            try:
                yield session
            finally:
                session.close()

        app = FastAPI()
        app.state.settings = self.settings
        app.include_router(
            create_webapp_router(
                auth_dependency=allow_test_user,
                session_dependency=test_session,
            )
        )
        return TestClient(app)


class VideoRepositoryTests(WebAppReadTestCase):
    def test_cursor_pagination_skips_deleted_and_missing_files(self) -> None:
        base = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
        with self.sessions() as session:
            self.add_job(session)
            valid_ids: list[int] = []
            for index in range(5):
                row = self.add_video(
                    session,
                    path=self.make_video_file(f"entrance/{index}.mp4"),
                    created_at=base + timedelta(minutes=index),
                )
                valid_ids.append(row.id)
            self.add_video(
                session,
                path=self.storage_path / "entrance/missing.mp4",
                created_at=base + timedelta(minutes=10),
            )
            self.add_video(
                session,
                path=self.make_video_file("entrance/deleted.mp4"),
                created_at=base + timedelta(minutes=11),
                deleted_at=base + timedelta(minutes=12),
            )
            session.commit()

            repository = VideoRepository(session, self.storage_path)
            first = repository.list_videos(
                camera_ids=["entrance"],
                limit=2,
            )
            second = repository.list_videos(
                camera_ids=["entrance"],
                cursor=first.next_cursor,
                limit=2,
            )
            third = repository.list_videos(
                camera_ids=["entrance"],
                cursor=second.next_cursor,
                limit=2,
            )

        self.assertEqual(
            [item.id for item in first.items + second.items + third.items],
            list(reversed(valid_ids)),
        )
        self.assertIsNotNone(first.next_cursor)
        self.assertIsNotNone(second.next_cursor)
        self.assertIsNone(third.next_cursor)

    def test_cursor_is_bound_to_camera_filter(self) -> None:
        now = datetime.now(UTC)
        with self.sessions() as session:
            self.add_job(session)
            self.add_video(
                session,
                path=self.make_video_file("one.mp4"),
                created_at=now,
            )
            self.add_video(
                session,
                path=self.make_video_file("two.mp4"),
                created_at=now - timedelta(seconds=1),
            )
            session.commit()
            repository = VideoRepository(session, self.storage_path)
            page = repository.list_videos(
                camera_ids=["entrance"],
                camera_id="entrance",
                limit=1,
            )

            with self.assertRaises(InvalidVideoCursor):
                repository.list_videos(
                    camera_ids=["entrance"],
                    cursor=page.next_cursor,
                    limit=1,
                )

    def test_file_must_be_inside_storage_and_camera_must_be_enabled(self) -> None:
        now = datetime.now(UTC)
        outside_path = Path(self.temp_dir.name) / "outside.mp4"
        outside_path.write_bytes(b"secret")
        with self.sessions() as session:
            self.add_job(session)
            outside = self.add_video(
                session,
                path=outside_path,
                created_at=now,
            )
            private = self.add_video(
                session,
                path=self.make_video_file("private.mp4"),
                camera_id="private",
                created_at=now,
            )
            session.commit()
            repository = VideoRepository(session, self.storage_path)

            with self.assertRaises(VideoFileUnavailable):
                repository.get_video_file(outside.id, camera_ids=["entrance"])
            with self.assertRaises(VideoNotFound):
                repository.get_video_file(private.id, camera_ids=["entrance"])


class ClimateServiceTests(WebAppReadTestCase):
    def test_current_climate_marks_stale_values(self) -> None:
        now = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
        with self.sessions() as session:
            update_temperature(session, "bedroom", 23.5, "°C")
            update_humidity(session, "bedroom", 48.0, "%")
            session.flush()
            for row in session.query(AppState):
                row.updated_at = now - timedelta(minutes=3)
            session.commit()

            result = get_current_climate(self.settings, session, now=now)

        self.assertEqual(len(result.rooms), 1)
        self.assertEqual(result.rooms[0].temperature.value, 23.5)  # type: ignore[union-attr]
        self.assertTrue(result.rooms[0].temperature.stale)  # type: ignore[union-attr]
        self.assertTrue(result.rooms[0].humidity.stale)  # type: ignore[union-attr]

    def test_history_is_downsampled_and_limited(self) -> None:
        end = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)
        start = end - timedelta(hours=2)
        with self.sessions() as session:
            for minute in range(120):
                recorded_at = start + timedelta(minutes=minute)
                session.add_all(
                    [
                        SensorReading(
                            room_id="bedroom",
                            metric="temperature",
                            value=20 + minute / 100,
                            unit="°C",
                            recorded_at=recorded_at,
                            received_at=recorded_at,
                        ),
                        SensorReading(
                            room_id="bedroom",
                            metric="humidity",
                            value=40 + minute / 100,
                            unit="%",
                            recorded_at=recorded_at,
                            received_at=recorded_at,
                        ),
                    ]
                )
            session.commit()

            result = get_climate_history(
                self.settings,
                session,
                room_id="bedroom",
                from_=start,
                to=end,
                point_limit=10,
            )

        self.assertGreater(result.bucket_sec, 60)
        self.assertEqual({series.metric for series in result.series}, {"temperature", "humidity"})
        for series in result.series:
            self.assertLessEqual(len(series.points), 10)
            self.assertEqual(sum(point.sample_count for point in series.points), 120)

    def test_history_rejects_unknown_room_and_oversized_window(self) -> None:
        now = datetime.now(UTC)
        with self.sessions() as session:
            with self.assertRaises(UnknownClimateRoom):
                get_climate_history(
                    self.settings,
                    session,
                    room_id="unknown",
                    now=now,
                )
            with self.assertRaises(InvalidClimateHistory):
                get_climate_history(
                    self.settings,
                    session,
                    room_id="bedroom",
                    from_=now - timedelta(days=31),
                    to=now,
                )


class ReadApiTests(WebAppReadTestCase):
    def test_video_list_does_not_expose_paths_and_file_supports_range(self) -> None:
        now = datetime.now(UTC)
        with self.sessions() as session:
            self.add_job(session)
            video = self.add_video(
                session,
                path=self.make_video_file("entrance/range.mp4"),
                created_at=now,
            )
            session.commit()
            video_id = video.id

        with self.make_api_client() as client:
            listing = client.get("/api/webapp/v1/videos")
            partial = client.get(
                f"/api/webapp/v1/videos/{video_id}/content",
                headers={"Range": "bytes=2-5"},
            )

        self.assertEqual(listing.status_code, 200)
        item = listing.json()["items"][0]
        self.assertNotIn("path", item)
        self.assertEqual(item["id"], video_id)
        self.assertEqual(partial.status_code, 206)
        self.assertEqual(partial.content, b"2345")
        self.assertEqual(partial.headers["content-range"], "bytes 2-5/10")

    def test_missing_and_disabled_video_files_are_not_accessible(self) -> None:
        now = datetime.now(UTC)
        with self.sessions() as session:
            self.add_job(session)
            missing = self.add_video(
                session,
                path=self.storage_path / "missing.mp4",
                created_at=now,
            )
            private = self.add_video(
                session,
                path=self.make_video_file("private.mp4"),
                camera_id="private",
                created_at=now,
            )
            session.commit()
            missing_id = missing.id
            private_id = private.id

        with self.make_api_client() as client:
            missing_response = client.get(
                f"/api/webapp/v1/videos/{missing_id}/content"
            )
            private_response = client.get(
                f"/api/webapp/v1/videos/{private_id}/content"
            )

        self.assertEqual(missing_response.status_code, 410)
        self.assertEqual(private_response.status_code, 404)

    def test_climate_history_uses_from_alias(self) -> None:
        end = datetime.now(UTC)
        start = end - timedelta(hours=1)
        with self.make_api_client() as client:
            response = client.get(
                "/api/webapp/v1/climate/history",
                params={
                    "room_id": "bedroom",
                    "from": start.isoformat(),
                    "to": end.isoformat(),
                    "point_limit": 10,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("from", response.json())
        self.assertNotIn("from_", response.json())

    def test_default_authentication_dependency_fails_closed(self) -> None:
        app = FastAPI()
        app.state.settings = self.settings
        app.include_router(create_webapp_router())

        with TestClient(app) as client:
            response = client.get("/api/webapp/v1/cameras")

        self.assertEqual(response.status_code, 503)
