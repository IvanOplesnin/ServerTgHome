from __future__ import annotations

import shutil
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from server_tg_home.core.config import CameraConfig, Settings
from server_tg_home.media.storage import make_clip_path, make_snapshot_path, make_tmp_path


class MediaError(RuntimeError):
    pass


def _run_ffmpeg(command: list[str], timeout_sec: int) -> None:
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_sec,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr[-2000:] if result.stderr else "unknown ffmpeg error"
        raise MediaError(stderr)


def capture_rtsp_clip(
    camera: CameraConfig,
    output_path: Path,
    duration_sec: int,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        *camera.ffmpeg_input_args,
        "-i",
        camera.rtsp_url,
        "-t",
        str(duration_sec),
        *camera.ffmpeg_output_args,
        str(output_path),
    ]
    _run_ffmpeg(command, timeout_sec=max(duration_sec + 60, 90))


def capture_snapshot(camera: CameraConfig, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        *camera.ffmpeg_input_args,
        "-i",
        camera.rtsp_url,
        "-frames:v",
        "1",
        "-q:v",
        "3",
        str(output_path),
    ]
    _run_ffmpeg(command, timeout_sec=30)


def buffer_dir(settings: Settings, camera_id: str) -> Path:
    path = settings.buffer.path / camera_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_buffer_command(settings: Settings, camera_id: str, camera: CameraConfig) -> list[str]:
    output_pattern = buffer_dir(settings, camera_id) / "%Y%m%d-%H%M%S.mp4"
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-y",
        *camera.ffmpeg_input_args,
        "-i",
        camera.rtsp_url,
        "-an",
        "-c:v",
        "copy",
        "-f",
        "segment",
        "-segment_time",
        str(settings.buffer.segment_seconds),
        "-reset_timestamps",
        "1",
        "-strftime",
        "1",
        str(output_pattern),
    ]


def cleanup_old_buffer_segments(settings: Settings, camera_id: str) -> None:
    cutoff = datetime.now(UTC) - timedelta(seconds=settings.buffer.keep_seconds)
    for segment in buffer_dir(settings, camera_id).glob("*.mp4"):
        try:
            if datetime.fromtimestamp(segment.stat().st_mtime, UTC) < cutoff:
                segment.unlink(missing_ok=True)
        except FileNotFoundError:
            continue


def select_pre_event_segments(
    settings: Settings,
    camera_id: str,
    event_time: datetime,
    pre_event_sec: int,
) -> list[Path]:
    start = event_time - timedelta(seconds=pre_event_sec + 1)
    end = event_time + timedelta(seconds=1)
    segments: list[Path] = []
    for segment in buffer_dir(settings, camera_id).glob("*.mp4"):
        try:
            mtime = datetime.fromtimestamp(segment.stat().st_mtime, UTC)
        except FileNotFoundError:
            continue
        if start <= mtime <= end:
            segments.append(segment)
    segments.sort(key=lambda item: item.stat().st_mtime)
    max_segments = max(pre_event_sec // max(settings.buffer.segment_seconds, 1) + 3, 1)
    return segments[-max_segments:]


def _concat_file_line(path: Path) -> str:
    escaped = str(path.resolve()).replace("'", "'\\''")
    return f"file '{escaped}'"


def concat_clips(parts: list[Path], output_path: Path) -> None:
    if not parts:
        raise MediaError("No clips to concatenate")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    concat_list = output_path.with_suffix(".concat.txt")
    concat_list.write_text("\n".join(_concat_file_line(part) for part in parts), encoding="utf-8")
    try:
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            str(output_path),
        ]
        _run_ffmpeg(command, timeout_sec=120)
    finally:
        concat_list.unlink(missing_ok=True)


def parse_event_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def record_event_clip(
    settings: Settings,
    camera_id: str,
    job_id: str,
    duration_sec: int,
    pre_event_sec: int,
    event_time_value: str | None,
) -> Path:
    camera = settings.cameras[camera_id]
    output_path = make_clip_path(settings, camera_id, job_id)

    use_buffer = settings.buffer.enabled and camera.buffer_enabled and pre_event_sec > 0
    event_time = parse_event_time(event_time_value)
    pre_segments = select_pre_event_segments(settings, camera_id, event_time, pre_event_sec) if use_buffer else []
    live_duration = max(duration_sec - pre_event_sec, 1) if pre_segments else duration_sec

    live_path = make_tmp_path(settings, f"{job_id}_live.mp4")
    capture_rtsp_clip(camera, live_path, live_duration)

    if pre_segments:
        try:
            concat_clips([*pre_segments, live_path], output_path)
        except MediaError:
            shutil.copy2(live_path, output_path)
    else:
        shutil.move(str(live_path), str(output_path))

    live_path.unlink(missing_ok=True)
    return output_path


def record_snapshot(settings: Settings, camera_id: str, job_id: str) -> Path:
    camera = settings.cameras[camera_id]
    output_path = make_snapshot_path(settings, camera_id, job_id)
    capture_snapshot(camera, output_path)
    return output_path
