from __future__ import annotations

from sqlalchemy.orm import Session

from server_tg_home.core.config import EventConfig, Settings
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
) -> str:
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
