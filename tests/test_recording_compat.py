from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from fractions import Fraction
from io import StringIO
from pathlib import Path
import stat
import struct
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from server_tg_home.core.config import Settings
from server_tg_home.database.models import Job, Video
from server_tg_home.database.session import Base
from server_tg_home.tools import recording_compat
from server_tg_home.tools.recording_compat import (
    MediaInfo,
    MigrationConfigurationError,
    MigrationOptions,
)


OLD_CONTENT = b"original-incompatible-recording"
NEW_CONTENT = b"converted-browser-compatible-recording"


def _media_info(
    *,
    compatible: bool,
    duration_sec: float = 10.0,
) -> MediaInfo:
    if compatible:
        return MediaInfo(
            format_names=frozenset({"mov", "mp4"}),
            duration_sec=duration_sec,
            video_stream_count=1,
            video_codec="h264",
            video_profile="High",
            video_level=41,
            width=1920,
            height=1066,
            pixel_format="yuv420p",
            fps=Fraction(20, 1),
            audio_stream_count=1,
            audio_codec="aac",
            audio_sample_rate=48_000,
            audio_channels=1,
            moov_before_mdat=True,
        )
    return MediaInfo(
        format_names=frozenset({"mov", "mp4"}),
        duration_sec=duration_sec,
        video_stream_count=1,
        video_codec="h264",
        video_profile="High",
        video_level=50,
        width=2880,
        height=1600,
        pixel_format="yuvj420p",
        fps=Fraction(20, 1),
        audio_stream_count=1,
        audio_codec="aac",
        audio_sample_rate=8_000,
        audio_channels=1,
        moov_before_mdat=True,
    )


def _atom(atom_type: bytes, payload: bytes = b"") -> bytes:
    return struct.pack(">I4s", len(payload) + 8, atom_type) + payload


class RecordingCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.base_path = Path(self.temp_dir.name)
        self.storage_path = self.base_path / "clips"
        self.storage_path.mkdir()
        self.backup_path = self.base_path / "migration-backups" / "run-1"
        self.database_path = self.base_path / "recordings.sqlite"
        self.database_url = f"sqlite+pysqlite:///{self.database_path}"
        self.engine = create_engine(self.database_url, future=True)
        Base.metadata.create_all(self.engine)
        self.settings = Settings(
            _env_file=None,
            app={"database_url": self.database_url},
            storage={"path": self.storage_path},
            cameras={
                "entrance": {
                    "rtsp_url": "rtsp://camera.invalid/stream",
                }
            },
        )

    def tearDown(self) -> None:
        self.engine.dispose()
        self.temp_dir.cleanup()

    def add_video(
        self,
        *,
        video_id: int,
        content: bytes,
        deleted: bool = False,
        path: Path | None = None,
    ) -> Path:
        video_path = path or (
            self.storage_path
            / "entrance"
            / "2026-07-27"
            / f"recording-{video_id}.mp4"
        )
        video_path.parent.mkdir(parents=True, exist_ok=True)
        video_path.write_bytes(content)
        with Session(self.engine) as session:
            session.add(
                Job(
                    id=f"job-{video_id}",
                    type="record_video_file",
                    source="test",
                    status="done",
                    payload={},
                )
            )
            session.add(
                Video(
                    id=video_id,
                    job_id=f"job-{video_id}",
                    camera_id="entrance",
                    path=str(video_path),
                    size_bytes=len(content),
                    duration_sec=10,
                    deleted_at=datetime.now(UTC) if deleted else None,
                )
            )
            session.commit()
        return video_path

    @staticmethod
    def fake_probe(path: Path) -> MediaInfo:
        return _media_info(
            compatible=path.read_bytes() == NEW_CONTENT,
        )

    @staticmethod
    def fake_transcode(
        source: Path,
        output: Path,
        *,
        source_info: MediaInfo,
    ) -> None:
        if source.read_bytes() != OLD_CONTENT:
            raise AssertionError("unexpected transcode source")
        if source_info.video_level != 50:
            raise AssertionError("unexpected source probe")
        output.write_bytes(NEW_CONTENT)

    def options(self, *, apply: bool) -> MigrationOptions:
        return MigrationOptions(
            camera_id="entrance",
            backup_dir=self.backup_path,
            apply=apply,
        )

    def database_video(self, video_id: int) -> Video:
        with Session(self.engine) as session:
            return session.execute(
                select(Video).where(Video.id == video_id)
            ).scalar_one()

    def test_dry_run_only_selects_active_rows_and_does_not_write(self) -> None:
        incompatible = self.add_video(video_id=1, content=OLD_CONTENT)
        compatible = self.add_video(video_id=2, content=NEW_CONTENT)
        deleted = self.add_video(
            video_id=3,
            content=OLD_CONTENT,
            deleted=True,
        )

        with patch.object(
            recording_compat,
            "_probe_media",
            side_effect=self.fake_probe,
        ):
            summary = recording_compat.run_migration(
                self.settings,
                self.options(apply=False),
            )

        self.assertEqual(summary.selected, 2)
        self.assertEqual(summary.pending, 1)
        self.assertEqual(summary.compatible, 1)
        self.assertEqual(summary.migrated, 0)
        self.assertEqual(summary.failed, 0)
        self.assertEqual(incompatible.read_bytes(), OLD_CONTENT)
        self.assertEqual(compatible.read_bytes(), NEW_CONTENT)
        self.assertEqual(deleted.read_bytes(), OLD_CONTENT)
        self.assertFalse(self.backup_path.exists())
        self.assertFalse(
            (self.storage_path.parent / recording_compat.LOCK_FILENAME).exists()
        )

    def test_apply_keeps_hardlink_backup_and_atomically_replaces_file(self) -> None:
        source = self.add_video(video_id=10, content=OLD_CONTENT)
        source.chmod(0o640)
        original_mtime_ns = 1_700_000_000_123_456_789
        source_stat = source.stat()
        source.touch()
        source.chmod(0o640)
        source_atime_ns = source_stat.st_atime_ns
        recording_compat.os.utime(
            source,
            ns=(source_atime_ns, original_mtime_ns),
        )

        with (
            patch.object(
                recording_compat,
                "_probe_media",
                side_effect=self.fake_probe,
            ),
            patch.object(
                recording_compat,
                "_run_transcode",
                side_effect=self.fake_transcode,
            ),
        ):
            summary = recording_compat.run_migration(
                self.settings,
                self.options(apply=True),
            )

        backup = self.backup_path / source.relative_to(self.storage_path)
        self.assertEqual(summary.selected, 1)
        self.assertEqual(summary.pending, 1)
        self.assertEqual(summary.migrated, 1)
        self.assertEqual(summary.failed, 0)
        self.assertEqual(source.read_bytes(), NEW_CONTENT)
        self.assertEqual(backup.read_bytes(), OLD_CONTENT)
        self.assertFalse(source.samefile(backup))
        self.assertEqual(stat.S_IMODE(source.stat().st_mode), 0o640)
        self.assertEqual(source.stat().st_mtime_ns, original_mtime_ns)
        self.assertEqual(
            self.database_video(10).size_bytes,
            len(NEW_CONTENT),
        )
        self.assertEqual(
            list(source.parent.glob(f"*{recording_compat.TEMP_FILE_MARKER}*")),
            [],
        )

    def test_resume_skips_compatible_file_and_repairs_database_size(self) -> None:
        source = self.add_video(video_id=11, content=NEW_CONTENT)
        with Session(self.engine) as session:
            row = session.get(Video, 11)
            assert row is not None
            row.size_bytes = 1
            session.commit()

        with (
            patch.object(
                recording_compat,
                "_probe_media",
                side_effect=self.fake_probe,
            ),
            patch.object(
                recording_compat,
                "_run_transcode",
                side_effect=AssertionError("must not transcode"),
            ),
        ):
            summary = recording_compat.run_migration(
                self.settings,
                self.options(apply=True),
            )

        self.assertEqual(summary.selected, 1)
        self.assertEqual(summary.compatible, 1)
        self.assertEqual(summary.pending, 1)
        self.assertEqual(summary.migrated, 0)
        self.assertEqual(summary.metadata_updated, 1)
        self.assertEqual(summary.failed, 0)
        self.assertEqual(source.read_bytes(), NEW_CONTENT)
        self.assertEqual(
            self.database_video(11).size_bytes,
            len(NEW_CONTENT),
        )

    def test_failed_validation_leaves_original_and_resumable_backup(self) -> None:
        source = self.add_video(video_id=12, content=OLD_CONTENT)

        def always_incompatible(path: Path) -> MediaInfo:
            del path
            return _media_info(compatible=False)

        with (
            patch.object(
                recording_compat,
                "_probe_media",
                side_effect=always_incompatible,
            ),
            patch.object(
                recording_compat,
                "_run_transcode",
                side_effect=self.fake_transcode,
            ),
        ):
            summary = recording_compat.run_migration(
                self.settings,
                self.options(apply=True),
            )

        backup = self.backup_path / source.relative_to(self.storage_path)
        self.assertEqual(summary.migrated, 0)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(
            summary.failure_reasons["converted_media_incompatible"],
            1,
        )
        self.assertEqual(source.read_bytes(), OLD_CONTENT)
        self.assertEqual(backup.read_bytes(), OLD_CONTENT)
        self.assertTrue(source.samefile(backup))
        self.assertEqual(self.database_video(12).size_bytes, len(OLD_CONTENT))
        self.assertEqual(
            list(source.parent.glob(f"*{recording_compat.TEMP_FILE_MARKER}*")),
            [],
        )

        with (
            patch.object(
                recording_compat,
                "_probe_media",
                side_effect=self.fake_probe,
            ),
            patch.object(
                recording_compat,
                "_run_transcode",
                side_effect=self.fake_transcode,
            ),
        ):
            resumed = recording_compat.run_migration(
                self.settings,
                self.options(apply=True),
            )

        self.assertEqual(resumed.migrated, 1)
        self.assertEqual(resumed.failed, 0)
        self.assertEqual(source.read_bytes(), NEW_CONTENT)
        self.assertEqual(backup.read_bytes(), OLD_CONTENT)
        self.assertFalse(source.samefile(backup))

    def test_existing_non_hardlink_backup_is_a_safe_collision(self) -> None:
        source = self.add_video(video_id=14, content=OLD_CONTENT)
        backup = self.backup_path / source.relative_to(self.storage_path)
        backup.parent.mkdir(parents=True)
        backup.write_bytes(b"unrelated-existing-backup")

        with (
            patch.object(
                recording_compat,
                "_probe_media",
                side_effect=self.fake_probe,
            ),
            patch.object(
                recording_compat,
                "_run_transcode",
                side_effect=AssertionError("collision must stop transcode"),
            ),
        ):
            summary = recording_compat.run_migration(
                self.settings,
                self.options(apply=True),
            )

        self.assertEqual(summary.migrated, 0)
        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.failure_reasons["backup_collision"], 1)
        self.assertEqual(source.read_bytes(), OLD_CONTENT)
        self.assertEqual(backup.read_bytes(), b"unrelated-existing-backup")
        self.assertFalse(source.samefile(backup))
        self.assertEqual(self.database_video(14).size_bytes, len(OLD_CONTENT))

    def test_unsafe_database_path_is_rejected_without_probe(self) -> None:
        outside = self.base_path / "outside.mp4"
        self.add_video(
            video_id=13,
            content=OLD_CONTENT,
            path=outside,
        )

        with patch.object(
            recording_compat,
            "_probe_media",
            side_effect=AssertionError("unsafe path must not be probed"),
        ):
            summary = recording_compat.run_migration(
                self.settings,
                self.options(apply=False),
            )

        self.assertEqual(summary.failed, 1)
        self.assertEqual(summary.failure_reasons["unsafe_recording_path"], 1)
        self.assertEqual(outside.read_bytes(), OLD_CONTENT)

    def test_backup_must_be_disjoint_from_storage(self) -> None:
        with self.assertRaises(MigrationConfigurationError):
            recording_compat.run_migration(
                self.settings,
                MigrationOptions(
                    camera_id="entrance",
                    backup_dir=self.storage_path / "backup",
                    apply=False,
                ),
            )

    def test_browser_compatibility_enforces_every_required_property(self) -> None:
        compatible = _media_info(compatible=True)
        self.assertTrue(recording_compat._is_browser_compatible(compatible))
        incompatible_variants = [
            replace(compatible, video_codec="hevc"),
            replace(compatible, video_profile="Main"),
            replace(compatible, video_level=50),
            replace(compatible, width=1922),
            replace(compatible, height=1082),
            replace(compatible, width=1919),
            replace(compatible, pixel_format="yuvj420p"),
            replace(compatible, fps=Fraction(31, 1)),
            replace(compatible, audio_codec="pcm_alaw"),
            replace(compatible, audio_sample_rate=8_000),
            replace(compatible, moov_before_mdat=False),
        ]
        for item in incompatible_variants:
            with self.subTest(item=item):
                self.assertFalse(
                    recording_compat._is_browser_compatible(item)
                )

    def test_faststart_parser_requires_moov_before_mdat(self) -> None:
        fast = self.base_path / "fast.mp4"
        slow = self.base_path / "slow.mp4"
        fast.write_bytes(
            _atom(b"ftyp", b"isom")
            + _atom(b"moov", b"metadata")
            + _atom(b"mdat", b"video")
        )
        slow.write_bytes(
            _atom(b"ftyp", b"isom")
            + _atom(b"mdat", b"video")
            + _atom(b"moov", b"metadata")
        )
        self.assertTrue(recording_compat._moov_before_mdat(fast))
        self.assertFalse(recording_compat._moov_before_mdat(slow))

    def test_ffmpeg_command_contains_compatibility_limits(self) -> None:
        command = recording_compat._build_ffmpeg_command(
            Path("/recordings/input.mp4"),
            Path("/recordings/output.mp4"),
        )

        self.assertEqual(command[command.index("-fpsmax") + 1], "30")
        self.assertEqual(command[command.index("-profile:v") + 1], "high")
        self.assertEqual(command[command.index("-level:v") + 1], "4.1")
        self.assertEqual(command[command.index("-pix_fmt") + 1], "yuv420p")
        self.assertEqual(command[command.index("-ar") + 1], "48000")
        self.assertEqual(command[command.index("-movflags") + 1], "+faststart")
        scale = command[command.index("-vf") + 1]
        self.assertIn("min(1920,iw)", scale)
        self.assertIn("min(1080,ih)", scale)
        self.assertIn("force_divisible_by=2", scale)
        self.assertNotIn("copy", command)

    def test_cli_summary_does_not_render_backup_or_recording_paths(self) -> None:
        summary = recording_compat.MigrationSummary(
            selected=1,
            pending=1,
        )
        stdout = StringIO()
        stderr = StringIO()
        with (
            patch.object(
                recording_compat,
                "load_settings",
                return_value=self.settings,
            ),
            patch.object(
                recording_compat,
                "run_migration",
                return_value=summary,
            ),
            patch("sys.stdout", stdout),
            patch("sys.stderr", stderr),
        ):
            exit_code = recording_compat.main(
                [
                    "--camera",
                    "entrance",
                    "--backup-dir",
                    str(self.backup_path),
                    "--dry-run",
                ]
            )

        self.assertEqual(exit_code, 0)
        output = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn(str(self.backup_path), output)
        self.assertNotIn(str(self.storage_path), output)


if __name__ == "__main__":
    unittest.main()
