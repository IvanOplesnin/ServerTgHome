from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server_tg_home.core.config import Settings
from server_tg_home.core.runtime_state import runtime_state_text
from server_tg_home.database.models import Job, Video
from server_tg_home.jobs.queue import JobQueue
from server_tg_home.media.storage import folder_size_bytes, format_bytes, iter_video_files


def build_status_text(settings: Settings, session: Session, queue: JobQueue) -> str:
    rows = session.execute(select(Job.status, func.count(Job.id)).group_by(Job.status)).all()
    counts = {status: count for status, count in rows}
    size = folder_size_bytes(settings.storage.path)
    max_size = settings.storage.max_size_bytes
    percent = (size / max_size * 100) if max_size else 0
    redis_status = "ok" if queue.ping() else "unavailable"

    video_count = session.scalar(select(func.count(Video.id)).where(Video.deleted_at.is_(None))) or 0
    latest_video = session.execute(
        select(Video).where(Video.deleted_at.is_(None)).order_by(Video.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    failed_jobs = session.execute(
        select(Job).where(Job.status == "failed").order_by(Job.updated_at.desc()).limit(3)
    ).scalars().all()

    lines = [
        "Status",
        f"Redis: {redis_status}",
        f"Queue: {queue.length()}",
        "Jobs queued/running/done/failed: "
        f"{counts.get('queued', 0)}/{counts.get('running', 0)}/"
        f"{counts.get('done', 0)}/{counts.get('failed', 0)}",
        f"Storage: {format_bytes(size)} / {format_bytes(max_size)} ({percent:.1f}%)",
        f"Videos: {video_count}",
    ]
    if latest_video is not None:
        lines.append(
            "Last video: "
            f"{latest_video.camera_id}, {_format_age(latest_video.created_at)}, "
            f"{format_bytes(latest_video.size_bytes)}"
        )
    lines.extend(["", runtime_state_text(session), "", build_cameras_text(settings)])
    if failed_jobs:
        lines.append("")
        lines.append("Recent failed jobs:")
        for job in failed_jobs:
            error = (job.error or "unknown error").replace("\n", " ")
            lines.append(f"- {job.type} {_format_age(job.updated_at)}: {error[:120]}")
    return "\n".join(lines)


def build_cameras_text(settings: Settings) -> str:
    if not settings.cameras:
        return "Cameras: none configured"

    lines = ["Cameras"]
    for camera_id, camera in settings.cameras.items():
        if not settings.buffer.enabled or not camera.buffer_enabled:
            lines.append(f"- {camera_id}: buffer disabled")
            continue

        buffer_path = settings.buffer.path / camera_id
        segments = sorted(buffer_path.glob("*.mp4")) if buffer_path.exists() else []
        if not segments:
            lines.append(f"- {camera_id}: no buffer segments")
            continue

        latest = _latest_existing_file(segments)
        if latest is None:
            lines.append(f"- {camera_id}: no readable buffer segments")
            continue

        try:
            latest_time = datetime.fromtimestamp(latest.stat().st_mtime, UTC)
        except FileNotFoundError:
            lines.append(f"- {camera_id}: no readable buffer segments")
            continue
        age_sec = max(0, int((datetime.now(UTC) - latest_time).total_seconds()))
        stale_after_sec = max(settings.buffer.keep_seconds, settings.buffer.segment_seconds * 3, 30)
        state = "ok" if age_sec <= stale_after_sec else "stale"
        buffer_size = folder_size_bytes(buffer_path)
        lines.append(
            f"- {camera_id}: {state}, last segment {_format_seconds(age_sec)} ago, "
            f"{len(segments)} segments, {format_bytes(buffer_size)}"
        )
    return "\n".join(lines)


def build_storage_text(settings: Settings) -> str:
    size = folder_size_bytes(settings.storage.path)
    max_size = settings.storage.max_size_bytes
    percent = (size / max_size * 100) if max_size else 0
    video_files = iter_video_files(settings.storage.path)
    return (
        "Storage\n"
        f"Path: {settings.storage.path}\n"
        f"Used: {format_bytes(size)} / {format_bytes(max_size)} ({percent:.1f}%)\n"
        f"Video files: {len(video_files)}"
    )


def _format_age(value: datetime | None) -> str:
    if value is None:
        return "never"
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    seconds = max(0, int((datetime.now(UTC) - value.astimezone(UTC)).total_seconds()))
    return f"{_format_seconds(seconds)} ago"


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


def _latest_existing_file(paths: list[Path]) -> Path | None:
    latest_path: Path | None = None
    latest_mtime = 0.0
    for path in paths:
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            continue
        if latest_path is None or mtime > latest_mtime:
            latest_path = path
            latest_mtime = mtime
    return latest_path
