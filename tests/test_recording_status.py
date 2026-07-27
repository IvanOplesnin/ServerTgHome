from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from server_tg_home.core.config import Settings
from server_tg_home.database.models import Job, Video
from server_tg_home.database.session import Base
from server_tg_home.jobs.recording_status import (
    RecordingActivity,
    build_recording_status_text,
    list_active_recordings,
    list_recent_recording_results,
)


NOW = datetime(2026, 7, 27, 12, 0, tzinfo=UTC)


class RecordingStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.settings = Settings(
            cameras={
                "entrance": {
                    "title": "Вход",
                    "rtsp_url": "rtsp://camera.invalid/entrance",
                },
                "living": {
                    "title": "Гостиная",
                    "rtsp_url": "rtsp://camera.invalid/living",
                },
            }
        )

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_query_returns_only_valid_active_video_jobs(self) -> None:
        with Session(self.engine) as session:
            session.add_all(
                [
                    self._job(
                        "running-archive",
                        job_type="record_video_file",
                        status="running",
                        camera_id="entrance",
                        duration_sec=90,
                        started_at=NOW,
                        phase="recording",
                    ),
                    self._job(
                        "clip",
                        job_type="record_and_send_video",
                        status="running",
                        camera_id="entrance",
                        duration_sec=20,
                        started_at=NOW,
                    ),
                    self._job(
                        "queued-archive",
                        job_type="record_video_file",
                        status="queued",
                        camera_id="living",
                        duration_sec=600,
                        started_at=NOW - timedelta(minutes=1),
                    ),
                    self._job(
                        "done",
                        job_type="record_video_file",
                        status="done",
                        camera_id="entrance",
                        duration_sec=20,
                    ),
                    self._job(
                        "snapshot",
                        job_type="snapshot_and_send",
                        status="running",
                        camera_id="entrance",
                        duration_sec=20,
                    ),
                    self._job(
                        "invalid-payload",
                        job_type="record_video_file",
                        status="running",
                        camera_id="entrance",
                        duration_sec=0,
                    ),
                    self._job(
                        "hidden-camera",
                        job_type="record_video_file",
                        status="queued",
                        camera_id="private",
                        duration_sec=20,
                    ),
                ]
            )
            session.commit()

            activities = list_active_recordings(
                session,
                camera_ids={"entrance", "living"},
                now=NOW,
            )

        self.assertEqual(
            [activity.job_id for activity in activities],
            ["running-archive", "queued-archive"],
        )
        self.assertEqual(activities[0].phase, "recording")
        self.assertEqual(activities[0].expected_finish_at, NOW + timedelta(seconds=90))
        self.assertEqual(activities[1].phase, "queued")
        self.assertIsNone(activities[1].started_at)
        self.assertIsNone(activities[1].expected_finish_at)

    def test_formatter_separates_running_queue_and_finalization(self) -> None:
        running = RecordingActivity(
            job_id="running",
            camera_id="entrance",
            status="running",
            phase="recording",
            duration_sec=600,
            created_at=NOW - timedelta(minutes=2),
            started_at=NOW - timedelta(minutes=2),
        )
        queued = RecordingActivity(
            job_id="queued",
            camera_id="living",
            status="queued",
            phase="queued",
            duration_sec=20,
            created_at=NOW,
            started_at=None,
        )

        text = build_recording_status_text(
            self.settings,
            [
                running,
                queued,
                RecordingActivity(
                    job_id="queued-2",
                    camera_id="living",
                    status="queued",
                    phase="queued",
                    duration_sec=20,
                    created_at=NOW,
                    started_at=None,
                ),
            ],
            now=NOW,
        )
        finalizing_activity = RecordingActivity(
            job_id="finalizing",
            camera_id="entrance",
            status="running",
            phase="finalizing",
            duration_sec=600,
            created_at=NOW - timedelta(minutes=11),
            started_at=NOW - timedelta(minutes=11),
        )
        finalizing = build_recording_status_text(
            self.settings,
            [finalizing_activity],
            now=NOW,
        )

        self.assertIn("Сейчас записываются камеры:", text)
        self.assertIn("Вход (entrance) — запись на SSD, осталось примерно 8 мин", text)
        self.assertIn("В очереди:", text)
        self.assertIn("Гостиная (living) — запись на SSD, 20 сек", text)
        self.assertIn("ещё заданий: 1", text)
        self.assertIn("Сохраняются файлы:", finalizing)
        self.assertIn("завершение и сохранение файла", finalizing)

    def test_formatter_reports_empty_and_stale_states(self) -> None:
        empty = build_recording_status_text(self.settings, [], now=NOW)
        stale = RecordingActivity(
            job_id="stale",
            camera_id="unknown",
            status="running",
            phase="stale",
            duration_sec=20,
            created_at=NOW - timedelta(hours=1),
            started_at=NOW - timedelta(hours=1),
        )
        stale_text = build_recording_status_text(
            self.settings,
            [stale],
            now=NOW,
        )

        self.assertEqual(empty, "Сейчас ни одна камера не записывается.")
        self.assertIn("Сейчас ни одна камера не записывается.", stale_text)
        self.assertIn("Проблемные задания:", stale_text)
        self.assertIn(
            (
                "unknown — задание не обновляется; "
                "новую запись можно запустить"
            ),
            stale_text,
        )

    def test_recent_results_only_include_miniapp_jobs(self) -> None:
        with Session(self.engine) as session:
            done = self._job(
                "done-miniapp",
                job_type="record_video_file",
                status="done",
                camera_id="entrance",
                duration_sec=20,
                source="telegram_mini_app",
                finished_at=NOW - timedelta(minutes=1),
            )
            failed = self._job(
                "failed-miniapp",
                job_type="record_video_file",
                status="failed",
                camera_id="living",
                duration_sec=20,
                source="telegram_mini_app",
                finished_at=NOW - timedelta(minutes=2),
            )
            other_source = self._job(
                "done-telegram",
                job_type="record_video_file",
                status="done",
                camera_id="entrance",
                duration_sec=20,
                source="telegram_record_command",
                finished_at=NOW,
            )
            session.add_all([done, failed, other_source])
            session.flush()
            session.add(
                Video(
                    job_id=done.id,
                    camera_id="entrance",
                    path="/data/clips/entrance/video.mp4",
                    size_bytes=100,
                    duration_sec=20,
                )
            )
            session.commit()

            results = list_recent_recording_results(
                session,
                camera_ids={"entrance", "living"},
                now=NOW,
            )

        self.assertEqual(
            [(result.job_id, result.status) for result in results],
            [
                ("done-miniapp", "done"),
                ("failed-miniapp", "failed"),
            ],
        )
        self.assertIsInstance(results[0].video_id, int)
        self.assertIsNone(results[1].video_id)

    @staticmethod
    def _job(
        job_id: str,
        *,
        job_type: str,
        status: str,
        camera_id: str,
        duration_sec: int,
        started_at: datetime | None = None,
        phase: str | None = None,
        source: str = "test",
        finished_at: datetime | None = None,
    ) -> Job:
        payload: dict[str, object] = {
            "camera_id": camera_id,
            "duration_sec": duration_sec,
        }
        if phase is not None:
            payload["recording_phase"] = phase
        return Job(
            id=job_id,
            type=job_type,
            source=source,
            status=status,
            payload=payload,
            created_at=NOW.replace(tzinfo=None),
            started_at=(
                started_at.replace(tzinfo=None)
                if started_at is not None
                else None
            ),
            finished_at=(
                finished_at.replace(tzinfo=None)
                if finished_at is not None
                else None
            ),
        )


if __name__ == "__main__":
    unittest.main()
