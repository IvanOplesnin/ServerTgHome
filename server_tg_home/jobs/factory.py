from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from server_tg_home.core.config import EventConfig, Settings
from server_tg_home.core.runtime_state import notifications_enabled
from server_tg_home.database.models import Job
from server_tg_home.jobs.queue import JobQueue
from server_tg_home.jobs.repository import create_job, iso_utc_now


def resolve_chat_ids(settings: Settings, event_chat_ids: list[int] | None = None) -> list[int]:
    if event_chat_ids:
        return event_chat_ids
    return settings.telegram.default_chat_ids


def resolve_message_thread_id(settings: Settings, message_thread_id: int | None = None) -> int | None:
    if message_thread_id is not None:
        return message_thread_id
    return settings.telegram.default_message_thread_id


def create_record_video_job(
    settings: Settings,
    session: Session,
    queue: JobQueue,
    *,
    source: str,
    camera_id: str,
    duration_sec: int,
    pre_event_sec: int | None,
    chat_ids: list[int] | None,
    message_thread_id: int | None,
    message: str | None,
    event_id: str | None = None,
    event_payload: dict | None = None,
    event_signature: str | None = None,
) -> str:
    if camera_id not in settings.cameras:
        raise ValueError(f"Unknown camera: {camera_id}")
    payload = {
        "camera_id": camera_id,
        "duration_sec": duration_sec,
        "pre_event_sec": pre_event_sec
        if pre_event_sec is not None
        else settings.buffer.pre_event_seconds,
        "chat_ids": resolve_chat_ids(settings, chat_ids),
        "message_thread_id": resolve_message_thread_id(settings, message_thread_id),
        "message": message,
        "event_id": event_id,
        "event_payload": event_payload or {},
        "event_signature": event_signature,
        "event_time": iso_utc_now(),
    }
    job = create_job(
        session,
        queue,
        job_type="record_and_send_video",
        source=source,
        payload=payload,
    )
    return job.id


def create_event_job(
    settings: Settings,
    session: Session,
    queue: JobQueue,
    *,
    event_id: str,
    event: EventConfig,
    event_payload: dict | None,
) -> str | None:
    enabled, _ = notifications_enabled(session)
    if not enabled:
        return None
    event_signature = build_event_signature(event_id, event_payload or {})
    if _is_event_suppressed(session, event_id, event_signature, event):
        return None
    return create_record_video_job(
        settings,
        session,
        queue,
        source="home_assistant",
        camera_id=event.camera_id,
        duration_sec=event.duration_sec,
        pre_event_sec=event.pre_event_sec,
        chat_ids=event.chat_ids,
        message_thread_id=event.message_thread_id,
        message=event.message,
        event_id=event_id,
        event_payload=event_payload,
        event_signature=event_signature,
    )


def create_snapshot_job(
    settings: Settings,
    session: Session,
    queue: JobQueue,
    *,
    source: str,
    camera_id: str,
    chat_ids: list[int] | None,
    message_thread_id: int | None,
    message: str | None = None,
) -> str:
    if camera_id not in settings.cameras:
        raise ValueError(f"Unknown camera: {camera_id}")
    job = create_job(
        session,
        queue,
        job_type="snapshot_and_send",
        source=source,
        payload={
            "camera_id": camera_id,
            "chat_ids": resolve_chat_ids(settings, chat_ids),
            "message_thread_id": resolve_message_thread_id(settings, message_thread_id),
            "message": message,
            "event_time": iso_utc_now(),
        },
    )
    return job.id


def create_sensor_graph_job(
    settings: Settings,
    session: Session,
    queue: JobQueue,
    *,
    source: str,
    room_id: str,
    metrics: list[str],
    window_sec: int,
    chat_ids: list[int] | None,
    message_thread_id: int | None,
) -> str:
    if room_id != "all" and room_id not in settings.temperatures.rooms:
        raise ValueError(f"Unknown room: {room_id}")
    allowed_metrics = {"temperature", "humidity"}
    unknown_metrics = [metric for metric in metrics if metric not in allowed_metrics]
    if unknown_metrics:
        raise ValueError(f"Unknown graph metric: {unknown_metrics[0]}")
    if not metrics:
        raise ValueError("At least one graph metric is required")

    job = create_job(
        session,
        queue,
        job_type="sensor_graph",
        source=source,
        payload={
            "room_id": room_id,
            "metrics": metrics,
            "window_sec": window_sec,
            "chat_ids": resolve_chat_ids(settings, chat_ids),
            "message_thread_id": resolve_message_thread_id(settings, message_thread_id),
            "event_time": iso_utc_now(),
        },
    )
    return job.id


def create_camera_audio_job(
    settings: Settings,
    session: Session,
    queue: JobQueue,
    *,
    source: str,
    camera_id: str,
    source_path: str,
    duration_sec: int,
    chat_ids: list[int] | None,
    message_thread_id: int | None,
    telegram_file_id: str | None = None,
    telegram_file_unique_id: str | None = None,
    telegram_message_id: int | None = None,
    sender_user_id: int | None = None,
    sender_name: str | None = None,
) -> str:
    if not settings.audio.enabled:
        raise ValueError("Audio playback is disabled.")
    camera = settings.cameras.get(camera_id)
    if camera is None:
        raise ValueError(f"Unknown camera: {camera_id}")
    if not camera.speaker_enabled:
        raise ValueError(f"Camera speaker is not enabled: {camera_id}")
    duration_sec = max(1, int(duration_sec))
    if duration_sec > settings.audio.max_duration_sec:
        raise ValueError(f"Voice message is too long. Max: {settings.audio.max_duration_sec}s.")

    job = create_job(
        session,
        queue,
        job_type="play_camera_audio",
        source=source,
        payload={
            "camera_id": camera_id,
            "source_path": source_path,
            "duration_sec": duration_sec,
            "chat_ids": resolve_chat_ids(settings, chat_ids),
            "message_thread_id": resolve_message_thread_id(settings, message_thread_id),
            "telegram_file_id": telegram_file_id,
            "telegram_file_unique_id": telegram_file_unique_id,
            "telegram_message_id": telegram_message_id,
            "sender_user_id": sender_user_id,
            "sender_name": sender_name,
            "event_time": iso_utc_now(),
        },
    )
    return job.id


def build_event_signature(event_id: str, event_payload: dict) -> str:
    data = {"event_id": event_id, "payload": event_payload}
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_event_suppressed(
    session: Session,
    event_id: str,
    event_signature: str,
    event: EventConfig,
) -> bool:
    now = datetime.now(UTC)
    lookback_sec = max(event.cooldown_sec, event.dedupe_window_sec, 0)
    if lookback_sec <= 0:
        return False

    cutoff = now - timedelta(seconds=lookback_sec)
    jobs = session.execute(
        select(Job)
        .where(Job.source == "home_assistant", Job.created_at >= cutoff)
        .order_by(Job.created_at.desc())
        .limit(100)
    ).scalars()

    for job in jobs:
        payload = job.payload or {}
        if payload.get("event_id") != event_id:
            continue
        age_sec = (now - _as_utc(job.created_at)).total_seconds()
        if event.cooldown_sec > 0 and age_sec <= event.cooldown_sec:
            return True
        if (
            event.dedupe_window_sec > 0
            and age_sec <= event.dedupe_window_sec
            and payload.get("event_signature") == event_signature
        ):
            return True
    return False


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def create_home_assistant_service_job(
    settings: Settings,
    session: Session,
    queue: JobQueue,
    *,
    source: str,
    domain: str,
    service: str,
    data: dict,
    chat_ids: list[int] | None,
    message_thread_id: int | None,
) -> str:
    job = create_job(
        session,
        queue,
        job_type="home_assistant_service",
        source=source,
        payload={
            "domain": domain,
            "service": service,
            "data": data,
            "chat_ids": resolve_chat_ids(settings, chat_ids),
            "message_thread_id": resolve_message_thread_id(settings, message_thread_id),
            "event_time": iso_utc_now(),
        },
    )
    return job.id
