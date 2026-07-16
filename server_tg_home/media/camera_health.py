from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from server_tg_home.core.config import Settings
from server_tg_home.media.storage import folder_size_bytes, format_bytes


@dataclass(frozen=True)
class CameraHealthStatus:
    camera_id: str
    state: str
    reason: str
    last_segment_at: datetime | None = None
    last_segment_age_sec: int | None = None
    segment_count: int = 0
    buffer_size_bytes: int = 0

    @property
    def ok(self) -> bool:
        return self.state == "ok"

    @property
    def notifiable(self) -> bool:
        return self.state in {"ok", "unavailable"}


def evaluate_camera_health(settings: Settings) -> list[CameraHealthStatus]:
    return [
        evaluate_single_camera_health(settings, camera_id)
        for camera_id in settings.cameras
    ]


def evaluate_single_camera_health(settings: Settings, camera_id: str) -> CameraHealthStatus:
    camera = settings.cameras[camera_id]
    if not settings.camera_health.enabled:
        return CameraHealthStatus(camera_id=camera_id, state="skipped", reason="healthcheck disabled")
    if not settings.buffer.enabled or not camera.buffer_enabled:
        return CameraHealthStatus(camera_id=camera_id, state="skipped", reason="buffer disabled")

    buffer_path = settings.buffer.path / camera_id
    segments = _buffer_segments(buffer_path)
    if not segments:
        return CameraHealthStatus(camera_id=camera_id, state="unavailable", reason="no buffer segments")

    latest = _latest_segment(segments)
    if latest is None:
        return CameraHealthStatus(camera_id=camera_id, state="unavailable", reason="no readable buffer segments")

    now = datetime.now(UTC)
    try:
        latest_mtime = datetime.fromtimestamp(latest.stat().st_mtime, UTC)
    except FileNotFoundError:
        return CameraHealthStatus(camera_id=camera_id, state="unavailable", reason="latest segment disappeared")

    age_sec = max(0, int((now - latest_mtime).total_seconds()))
    stale_after_sec = _stale_after_sec(settings)
    state = "ok" if age_sec <= stale_after_sec else "unavailable"
    reason = "fresh buffer" if state == "ok" else f"last buffer segment is older than {stale_after_sec}s"
    return CameraHealthStatus(
        camera_id=camera_id,
        state=state,
        reason=reason,
        last_segment_at=latest_mtime,
        last_segment_age_sec=age_sec,
        segment_count=len(segments),
        buffer_size_bytes=folder_size_bytes(buffer_path),
    )


def build_camera_health_text(settings: Settings) -> str:
    statuses = evaluate_camera_health(settings)
    if not statuses:
        return "Camera health\nNo cameras configured."

    lines = ["Camera health"]
    for status in statuses:
        lines.append(_format_camera_health_status(status))
    return "\n".join(lines)


def _format_camera_health_status(status: CameraHealthStatus) -> str:
    if status.state == "skipped":
        return f"- {status.camera_id}: skipped, {status.reason}"
    if status.last_segment_age_sec is None:
        return f"- {status.camera_id}: {status.state}, {status.reason}"
    return (
        f"- {status.camera_id}: {status.state}, last segment "
        f"{_format_seconds(status.last_segment_age_sec)} ago, "
        f"{status.segment_count} segments, {format_bytes(status.buffer_size_bytes)}"
    )


def _buffer_segments(buffer_path: Path) -> list[Path]:
    return sorted(buffer_path.glob("*.mp4")) if buffer_path.exists() else []


def _latest_segment(segments: list[Path]) -> Path | None:
    latest_path: Path | None = None
    latest_mtime = 0.0
    for path in segments:
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            continue
        if latest_path is None or mtime > latest_mtime:
            latest_path = path
            latest_mtime = mtime
    return latest_path


def _stale_after_sec(settings: Settings) -> int:
    configured = settings.camera_health.stale_after_sec
    if configured is not None and configured > 0:
        return configured
    return max(settings.buffer.keep_seconds, settings.buffer.segment_seconds * 3, 30)


def _format_seconds(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"
