from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from server_tg_home.database.models import Job, utcnow
from server_tg_home.jobs.queue import JobQueue


def create_job(
    session: Session,
    queue: JobQueue,
    *,
    job_type: str,
    source: str,
    payload: dict,
) -> Job:
    job = Job(
        id=str(uuid4()),
        type=job_type,
        source=source,
        status="queued",
        payload=payload,
        attempts=0,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(job)
    session.commit()
    queue.enqueue(job.id)
    return job


def load_job(session: Session, job_id: str) -> Job | None:
    return session.execute(select(Job).where(Job.id == job_id)).scalar_one_or_none()


def mark_running(job: Job) -> None:
    job.status = "running"
    job.attempts += 1
    job.error = None
    job.started_at = utcnow()
    job.updated_at = utcnow()


def mark_done(job: Job) -> None:
    job.status = "done"
    job.finished_at = utcnow()
    job.updated_at = utcnow()


def mark_failed(job: Job, error: str) -> None:
    job.status = "failed"
    job.error = error[:4000]
    job.finished_at = utcnow()
    job.updated_at = utcnow()


def mark_queued(job: Job, error: str) -> None:
    job.status = "queued"
    job.error = error[:4000]
    job.updated_at = utcnow()


def iso_utc_now() -> str:
    return datetime.now(UTC).isoformat()
