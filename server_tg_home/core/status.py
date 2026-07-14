from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from server_tg_home.core.config import Settings
from server_tg_home.database.models import Job
from server_tg_home.jobs.queue import JobQueue
from server_tg_home.media.storage import folder_size_bytes, format_bytes


def build_status_text(settings: Settings, session: Session, queue: JobQueue) -> str:
    rows = session.execute(select(Job.status, func.count(Job.id)).group_by(Job.status)).all()
    counts = {status: count for status, count in rows}
    size = folder_size_bytes(settings.storage.path)
    max_size = settings.storage.max_size_bytes
    percent = (size / max_size * 100) if max_size else 0
    redis_status = "ok" if queue.ping() else "unavailable"
    return (
        "Status\n"
        f"Redis: {redis_status}\n"
        f"Queue: {queue.length()}\n"
        f"Jobs queued/running/done/failed: "
        f"{counts.get('queued', 0)}/{counts.get('running', 0)}/"
        f"{counts.get('done', 0)}/{counts.get('failed', 0)}\n"
        f"Storage: {format_bytes(size)} / {format_bytes(max_size)} ({percent:.1f}%)"
    )
