from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Literal

from sqlalchemy import case, select
from sqlalchemy.orm import Session

from server_tg_home.core.config import Settings
from server_tg_home.database.models import Job, Video


RECORDING_JOB_TYPE = "record_video_file"
ACTIVE_RECORDING_STATUSES = frozenset({"queued", "running"})
MAX_RECORD_DURATION_SEC = 3600
MAX_ACTIVITY_QUERY_SIZE = 200
MAX_RECENT_RESULT_SIZE = 20
QUEUED_STALE_AFTER = timedelta(hours=24)
RECENT_RESULT_WINDOW = timedelta(hours=1)

RecordingState = Literal["queued", "running"]
RecordingPhase = Literal["queued", "recording", "finalizing", "stale"]
RecordingResultState = Literal["done", "failed"]


@dataclass(frozen=True)
class RecordingActivity:
    job_id: str
    camera_id: str
    status: RecordingState
    phase: RecordingPhase
    duration_sec: int
    created_at: datetime
    started_at: datetime | None

    @property
    def expected_finish_at(self) -> datetime | None:
        if self.status != "running" or self.started_at is None:
            return None
        return self.started_at + timedelta(seconds=self.duration_sec)

    @property
    def blocks_new_recording(self) -> bool:
        return self.phase != "stale"


@dataclass(frozen=True)
class RecordingResult:
    job_id: str
    camera_id: str
    status: RecordingResultState
    finished_at: datetime
    video_id: int | None


def list_active_recordings(
    session: Session,
    *,
    camera_ids: set[str] | None = None,
    now: datetime | None = None,
) -> list[RecordingActivity]:
    """Return bounded SSD recording activity without trusting arbitrary JSON."""

    now_utc = _as_utc(now or datetime.now(UTC))
    status_order = case((Job.status == "running", 0), else_=1)
    jobs = session.execute(
        select(Job)
        .where(
            Job.type == RECORDING_JOB_TYPE,
            Job.status.in_(ACTIVE_RECORDING_STATUSES),
        )
        .order_by(status_order, Job.created_at.asc(), Job.id.asc())
        .limit(MAX_ACTIVITY_QUERY_SIZE)
    ).scalars()

    activities: list[RecordingActivity] = []
    for job in jobs:
        activity = _activity_from_job(job, now=now_utc)
        if activity is None:
            continue
        if camera_ids is not None and activity.camera_id not in camera_ids:
            continue
        activities.append(activity)
    return activities


def list_recent_recording_results(
    session: Session,
    *,
    camera_ids: set[str] | None = None,
    now: datetime | None = None,
) -> list[RecordingResult]:
    now_utc = _as_utc(now or datetime.now(UTC))
    cutoff = now_utc - RECENT_RESULT_WINDOW
    rows = session.execute(
        select(Job, Video.id)
        .outerjoin(Video, Video.job_id == Job.id)
        .where(
            Job.type == RECORDING_JOB_TYPE,
            Job.source == "telegram_mini_app",
            Job.status.in_({"done", "failed"}),
            Job.finished_at.is_not(None),
            Job.finished_at >= cutoff,
        )
        .order_by(Job.finished_at.desc(), Job.id.desc())
        .limit(MAX_RECENT_RESULT_SIZE)
    )
    results: list[RecordingResult] = []
    for job, video_id in rows:
        payload = job.payload if isinstance(job.payload, dict) else {}
        camera_id = payload.get("camera_id")
        if (
            not isinstance(camera_id, str)
            or not camera_id
            or len(camera_id) > 128
            or job.finished_at is None
        ):
            continue
        if camera_ids is not None and camera_id not in camera_ids:
            continue
        result_status: RecordingResultState = (
            "done" if job.status == "done" else "failed"
        )
        results.append(
            RecordingResult(
                job_id=job.id,
                camera_id=camera_id,
                status=result_status,
                finished_at=_as_utc(job.finished_at),
                video_id=int(video_id) if video_id is not None else None,
            )
        )
    return results


def build_recording_status_text(
    settings: Settings,
    activities: list[RecordingActivity],
    *,
    now: datetime | None = None,
) -> str:
    now_utc = _as_utc(now or datetime.now(UTC))
    by_phase: dict[RecordingPhase, list[RecordingActivity]] = defaultdict(list)
    for activity in activities:
        by_phase[activity.phase].append(activity)

    lines: list[str] = []
    recording = by_phase["recording"]
    if recording:
        lines.append("Сейчас записываются камеры:")
        lines.extend(
            _format_grouped_lines(settings, recording, now=now_utc)
        )
    else:
        lines.append("Сейчас ни одна камера не записывается.")

    sections: tuple[tuple[RecordingPhase, str], ...] = (
        ("finalizing", "Сохраняются файлы:"),
        ("queued", "В очереди:"),
        ("stale", "Проблемные задания:"),
    )
    for phase, title in sections:
        phase_activities = by_phase[phase]
        if not phase_activities:
            continue
        lines.extend(
            [
                "",
                title,
                *_format_grouped_lines(
                    settings,
                    phase_activities,
                    now=now_utc,
                ),
            ]
        )
    return "\n".join(lines)


def _activity_from_job(
    job: Job,
    *,
    now: datetime,
) -> RecordingActivity | None:
    payload = job.payload if isinstance(job.payload, dict) else {}
    camera_id = payload.get("camera_id")
    duration_sec = payload.get("duration_sec")
    if (
        not isinstance(camera_id, str)
        or not camera_id
        or len(camera_id) > 128
        or isinstance(duration_sec, bool)
        or not isinstance(duration_sec, int)
        or duration_sec <= 0
        or job.status not in ACTIVE_RECORDING_STATUSES
        or job.type != RECORDING_JOB_TYPE
    ):
        return None

    status: RecordingState = "running" if job.status == "running" else "queued"
    created_at = _as_utc(job.created_at)
    started_at = (
        _as_utc(job.started_at)
        if status == "running" and job.started_at is not None
        else None
    )
    phase = _recording_phase(
        status=status,
        payload=payload,
        duration_sec=duration_sec,
        created_at=created_at,
        started_at=started_at,
        now=now,
    )
    return RecordingActivity(
        job_id=job.id,
        camera_id=camera_id,
        status=status,
        phase=phase,
        duration_sec=duration_sec,
        created_at=created_at,
        started_at=started_at,
    )


def _recording_phase(
    *,
    status: RecordingState,
    payload: dict,
    duration_sec: int,
    created_at: datetime,
    started_at: datetime | None,
    now: datetime,
) -> RecordingPhase:
    if status == "queued":
        return "stale" if now - created_at > QUEUED_STALE_AFTER else "queued"

    baseline = started_at or created_at
    timeout_sec = max(300, duration_sec * 4 + 120)
    if now > baseline + timedelta(seconds=timeout_sec):
        return "stale"

    stored_phase = payload.get("recording_phase")
    if stored_phase == "finalizing":
        return "finalizing"
    if stored_phase == "recording":
        return "recording"

    # Compatibility for a running job created by an older worker.
    if started_at is None:
        return "finalizing"
    expected_finish_at = started_at + timedelta(seconds=duration_sec)
    return "recording" if now <= expected_finish_at else "finalizing"


def _format_grouped_lines(
    settings: Settings,
    activities: list[RecordingActivity],
    *,
    now: datetime,
) -> list[str]:
    by_camera: dict[str, list[RecordingActivity]] = defaultdict(list)
    for activity in activities:
        by_camera[activity.camera_id].append(activity)

    lines: list[str] = []
    for camera_id, camera_activities in by_camera.items():
        activity = camera_activities[0]
        camera = settings.cameras.get(camera_id)
        title = camera.title if camera is not None else None
        camera_label = (
            f"{title} ({camera_id})"
            if title and title != camera_id
            else camera_id
        )
        detail = _format_activity_detail(activity, now=now)
        extra_count = len(camera_activities) - 1
        suffix = f", ещё заданий: {extra_count}" if extra_count else ""
        lines.append(f"• {camera_label} — {detail}{suffix}")
    return lines


def _format_activity_detail(
    activity: RecordingActivity,
    *,
    now: datetime,
) -> str:
    if activity.phase == "queued":
        return f"запись на SSD, {_format_interval(activity.duration_sec)}"
    if activity.phase == "finalizing":
        return "завершение и сохранение файла"
    if activity.phase == "stale":
        return "задание не обновляется; новую запись можно запустить"

    finish_at = activity.expected_finish_at
    if finish_at is None:
        return "запись на SSD, запуск"
    remaining_sec = ceil((finish_at - now).total_seconds())
    if remaining_sec <= 0:
        return "завершение записи"
    return f"запись на SSD, осталось примерно {_format_interval(remaining_sec)}"


def _format_interval(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} сек"
    minutes = ceil(seconds / 60)
    if minutes < 60:
        return f"{minutes} мин"
    hours = minutes // 60
    remaining_minutes = minutes % 60
    if remaining_minutes:
        return f"{hours} ч {remaining_minutes} мин"
    return f"{hours} ч"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
