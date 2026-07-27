from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from server_tg_home.core.config import CameraConfig
from server_tg_home.media.recorder import build_buffer_command, capture_rtsp_clip


class MediaCompatibilityTests(unittest.TestCase):
    def test_final_clip_uses_browser_compatible_output(self) -> None:
        camera = CameraConfig(rtsp_url="rtsp://camera.local/main")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "clip.mp4"
            with patch("server_tg_home.media.recorder._run_ffmpeg") as run:
                capture_rtsp_clip(camera, output, 20)

        command = run.call_args.args[0]
        filter_value = command[command.index("-vf") + 1]

        self.assertIn("min(1920,iw)", filter_value)
        self.assertIn("min(1080,ih)", filter_value)
        self.assertEqual(command[command.index("-fpsmax") + 1], "30")
        self.assertEqual(command[command.index("-c:v") + 1], "libx264")
        self.assertEqual(command[command.index("-profile:v") + 1], "high")
        self.assertEqual(command[command.index("-level:v") + 1], "4.1")
        self.assertEqual(command[command.index("-pix_fmt") + 1], "yuv420p")
        self.assertEqual(command[command.index("-ar") + 1], "48000")
        self.assertIn("+faststart", command)

    @unittest.skipUnless(
        shutil.which("ffmpeg") and shutil.which("ffprobe"),
        "ffmpeg and ffprobe are required",
    )
    def test_compatibility_args_produce_mobile_mp4(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            output = Path(directory) / "output.mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:size=2880x1600:rate=5",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=1000:sample_rate=8000",
                    "-t",
                    "0.4",
                    "-c:v",
                    "libx264",
                    "-profile:v",
                    "high",
                    "-level:v",
                    "5.0",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    str(source),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
            )

            camera = CameraConfig(
                rtsp_url=str(source),
                ffmpeg_input_args=[],
            )
            capture_rtsp_clip(camera, output, 1)
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_streams",
                    "-of",
                    "json",
                    str(output),
                ],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )

        streams = json.loads(result.stdout)["streams"]
        video = next(stream for stream in streams if stream["codec_type"] == "video")
        audio = next(stream for stream in streams if stream["codec_type"] == "audio")
        self.assertLessEqual(video["width"], 1920)
        self.assertLessEqual(video["height"], 1080)
        self.assertEqual(video["codec_name"], "h264")
        self.assertEqual(video["profile"], "High")
        self.assertEqual(video["level"], 41)
        self.assertEqual(video["pix_fmt"], "yuv420p")
        self.assertEqual(audio["codec_name"], "aac")
        self.assertEqual(audio["sample_rate"], "48000")

    def test_raw_pre_event_buffer_keeps_copy_codec(self) -> None:
        camera = CameraConfig(rtsp_url="rtsp://camera.local/main")
        settings = type(
            "SettingsStub",
            (),
            {
                "buffer": type(
                    "BufferStub",
                    (),
                    {
                        "path": Path("/tmp/server-tg-home-test-buffer"),
                        "segment_seconds": 1,
                    },
                )()
            },
        )()

        with patch("server_tg_home.media.recorder.buffer_dir") as buffer_dir:
            buffer_dir.return_value = Path("/tmp/server-tg-home-test-buffer/entrance")
            command = build_buffer_command(settings, "entrance", camera)

        self.assertEqual(command[command.index("-c:v") + 1], "copy")


if __name__ == "__main__":
    unittest.main()
