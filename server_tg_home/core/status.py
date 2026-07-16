from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server_tg_home.core.config import Settings
from server_tg_home.core.runtime_state import runtime_state_text
from server_tg_home.database.models import Job, Video
from server_tg_home.jobs.queue import JobQueue
from server_tg_home.media.camera_health import evaluate_camera_health
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
    for status in evaluate_camera_health(settings):
        if status.state == "skipped":
            lines.append(f"- {status.camera_id}: skipped, {status.reason}")
            continue
        if status.last_segment_age_sec is None:
            lines.append(f"- {status.camera_id}: {status.state}, {status.reason}")
            continue
        lines.append(
            f"- {status.camera_id}: {status.state}, "
            f"last segment {_format_seconds(status.last_segment_age_sec)} ago, "
            f"{status.segment_count} segments, {format_bytes(status.buffer_size_bytes)}"
        )
    return "\n".join(lines)


def build_storage_text(settings: Settings) -> str:
    clips_size = folder_size_bytes(settings.storage.path)
    buffer_size = folder_size_bytes(settings.buffer.path)
    graphs_size = folder_size_bytes(settings.graphs.path)
    max_size = settings.storage.max_size_bytes
    percent = (clips_size / max_size * 100) if max_size else 0
    video_files = iter_video_files(settings.storage.path)
    return "\n".join(
        [
            "Disk",
            f"Clips path: {settings.storage.path}",
            f"Clips used: {format_bytes(clips_size)} / {format_bytes(max_size)} ({percent:.1f}%)",
            f"Video files: {len(video_files)}",
            f"Buffer path: {settings.buffer.path}",
            f"Buffer used: {format_bytes(buffer_size)}",
            f"Graphs path: {settings.graphs.path}",
            f"Graphs used: {format_bytes(graphs_size)}",
            f"Warning threshold: {settings.storage.warning_threshold_percent}%",
            f"Cleanup target: {settings.storage.cleanup_target_percent}%",
        ]
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
