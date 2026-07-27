from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from server_tg_home.core.config import Settings
from server_tg_home.database.models import Job
from server_tg_home.jobs.processor import JobProcessor
from server_tg_home.jobs.repository import mark_queued


class RecordingJobLifecycleTests(unittest.TestCase):
    def test_worker_publishes_recording_and_finalizing_phases(self) -> None:
        with TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "recording.mp4"
            output.write_bytes(b"video")
            settings = Settings(
                cameras={
                    "entrance": {
                        "rtsp_url": "rtsp://camera.invalid/entrance",
                    }
                }
            )
            processor = object.__new__(JobProcessor)
            processor.settings = settings
            processor._notify_record_saved = MagicMock()
            session = MagicMock()
            job = Job(
                id="job-1",
                type="record_video_file",
                source="test",
                status="running",
                payload={
                    "camera_id": "entrance",
                    "duration_sec": 20,
                    "pre_event_sec": 0,
                    "chat_ids": [],
                },
            )

            def capture(*args, **kwargs):
                del args, kwargs
                self.assertEqual(job.payload["recording_phase"], "recording")
                return output

            with patch(
                "server_tg_home.jobs.processor.record_event_clip",
                side_effect=capture,
            ):
                processor._process_record_video_file(session, job)

        self.assertEqual(job.payload["recording_phase"], "finalizing")
        self.assertGreaterEqual(session.commit.call_count, 3)
        session.add.assert_called_once()
        processor._notify_record_saved.assert_called_once()

    def test_retry_clears_started_time_and_recording_phase(self) -> None:
        job = Job(
            id="job-1",
            type="record_video_file",
            source="test",
            status="running",
            payload={
                "camera_id": "entrance",
                "duration_sec": 20,
                "recording_phase": "recording",
            },
        )
        job.started_at = datetime.now(UTC)

        mark_queued(job, "temporary failure")

        self.assertEqual(job.status, "queued")
        self.assertNotIn("recording_phase", job.payload)
        self.assertIsNone(job.started_at)
        self.assertIsNone(job.finished_at)


if __name__ == "__main__":
    unittest.main()
