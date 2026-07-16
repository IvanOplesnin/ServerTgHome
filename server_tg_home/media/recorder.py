from __future__ import annotations

import logging
import shutil
import subprocess
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from server_tg_home.core.config import CameraConfig, Settings
from server_tg_home.media.storage import make_clip_path, make_snapshot_path, make_tmp_path


class MediaError(RuntimeError):
    pass


logger = logging.getLogger(__name__)
BufferSegment = tuple[datetime, datetime, Path]


def camera_input_url(camera: CameraConfig) -> str:
    return camera.ffmpeg_url or camera.rtsp_url


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
        camera_input_url(camera),
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
        camera_input_url(camera),
        "-ss",
        "2",
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-q:v",
        "3",
        "-update",
        "1",
        str(output_path),
    ]
    _run_ffmpeg(command, timeout_sec=45)


def capture_snapshot_from_video(input_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-sseof",
        "-0.2",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-frames:v",
        "1",
        "-q:v",
        "3",
        "-update",
        "1",
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
        camera_input_url(camera),
        *camera.ffmpeg_output_args,
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


def select_buffer_window_segments(
    settings: Settings,
    camera_id: str,
    start: datetime,
    end: datetime,
) -> list[BufferSegment]:
    segments: list[BufferSegment] = []
    for segment in buffer_dir(settings, camera_id).glob("*.mp4"):
        duration = probe_duration_sec(segment)
        if duration is None:
            continue
        segment_start = segment_start_time(segment)
        segment_end = segment_start + timedelta(seconds=duration)
        if segment_start <= end and segment_end >= start:
            segments.append((segment_start, segment_end, segment))
    segments.sort(key=lambda item: item[0])
    return segments


def segments_cover_window(
    segments: list[BufferSegment],
    start: datetime,
    end: datetime,
) -> bool:
    if not segments:
        return False
    tolerance = timedelta(seconds=1)
    first_start, covered_until, _ = segments[0]
    if first_start - tolerance > start:
        return False
    for segment_start, segment_end, _ in segments[1:]:
        if covered_until + tolerance < segment_start:
            return covered_until + tolerance >= end
        if segment_end > covered_until:
            covered_until = segment_end
        if covered_until + tolerance >= end:
            return True
    return covered_until + tolerance >= end


def wait_for_buffer_window(
    settings: Settings,
    camera_id: str,
    start: datetime,
    end: datetime,
    timeout_sec: int,
) -> list[BufferSegment]:
    deadline = time.monotonic() + timeout_sec
    while True:
        segments = select_buffer_window_segments(settings, camera_id, start, end)
        if segments_cover_window(segments, start, end):
            return segments
        if time.monotonic() >= deadline:
            return []
        time.sleep(1)


def segment_start_time(segment: Path) -> datetime:
    try:
        return datetime.strptime(segment.stem, "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return datetime.fromtimestamp(segment.stat().st_mtime, UTC)


def probe_duration_sec(path: Path) -> float | None:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        duration = float(result.stdout.strip())
    except ValueError:
        return None
    return duration if duration > 0 else None


def latest_snapshot_buffer_segment(settings: Settings, camera_id: str) -> Path | None:
    candidates: list[tuple[float, Path]] = []
    for segment in buffer_dir(settings, camera_id).glob("*.mp4"):
        try:
            candidates.append((segment.stat().st_mtime, segment))
        except FileNotFoundError:
            continue
    candidates.sort(key=lambda item: item[0], reverse=True)

    for _, segment in candidates:
        try:
            if segment.stat().st_size < 8 * 1024:
                continue
        except FileNotFoundError:
            continue
        duration = probe_duration_sec(segment)
        if duration is not None and duration >= 0.2:
            return segment
    return None


def _concat_file_line(path: Path) -> str:
    escaped = str(path.resolve()).replace("'", "'\\''")
    return f"file '{escaped}'"


def render_buffer_window(
    camera: CameraConfig,
    segments: list[BufferSegment],
    target_start: datetime,
    duration_sec: int,
    output_path: Path,
) -> None:
    if not segments:
        raise MediaError("No buffer segments to render")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    concat_list = output_path.with_suffix(".concat.txt")
    concat_list.write_text(
        "\n".join(_concat_file_line(segment_path) for _, _, segment_path in segments),
        encoding="utf-8",
    )
    first_segment_start = segments[0][0]
    offset_sec = max(0.0, (target_start - first_segment_start).total_seconds())
    try:
        command = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "warning",
            "-y",
            "-fflags",
            "+genpts",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-ss",
            f"{offset_sec:.3f}",
            "-t",
            str(duration_sec),
            *camera.ffmpeg_clip_output_args,
            str(output_path),
        ]
        _run_ffmpeg(command, timeout_sec=max(duration_sec * 4 + 60, 120))
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
    pre_event_sec = max(0, min(pre_event_sec, duration_sec))

    use_buffer = settings.buffer.enabled and camera.buffer_enabled and pre_event_sec > 0
    event_time = parse_event_time(event_time_value)
    target_start = event_time - timedelta(seconds=pre_event_sec)
    target_end = target_start + timedelta(seconds=duration_sec)

    if use_buffer:
        wait_timeout_sec = int(max((target_end - datetime.now(UTC)).total_seconds(), 0) + 30)
        logger.info(
            "Waiting for buffer window camera=%s start=%s end=%s timeout=%ss",
            camera_id,
            target_start.isoformat(),
            target_end.isoformat(),
            wait_timeout_sec,
        )
        segments = wait_for_buffer_window(
            settings,
            camera_id,
            target_start,
            target_end,
            max(wait_timeout_sec, 5),
        )
        if segments:
            try:
                render_buffer_window(camera, segments, target_start, duration_sec, output_path)
                return output_path
            except MediaError:
                logger.exception("Failed to render buffered clip for camera %s", camera_id)
        else:
            logger.warning("Buffer window unavailable for camera %s; falling back to live capture", camera_id)

    live_path = make_tmp_path(settings, f"{job_id}_live.mp4")
    try:
        capture_rtsp_clip(camera, live_path, duration_sec)
        shutil.move(str(live_path), str(output_path))
    finally:
        live_path.unlink(missing_ok=True)
    return output_path


def record_snapshot(settings: Settings, camera_id: str, job_id: str) -> Path:
    camera = settings.cameras[camera_id]
    output_path = make_snapshot_path(settings, camera_id, job_id)
    if settings.buffer.enabled and camera.buffer_enabled:
        segment = latest_snapshot_buffer_segment(settings, camera_id)
        if segment is not None:
            try:
                capture_snapshot_from_video(segment, output_path)
                return output_path
            except MediaError:
                logger.exception("Failed to capture snapshot from buffer for camera %s", camera_id)
    capture_snapshot(camera, output_path)
    return output_path
