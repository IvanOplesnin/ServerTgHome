from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from server_tg_home.database.models import Job, utcnow
from server_tg_home.jobs.queue import JobQueue


class JobEnqueueError(RuntimeError):
    """The database job could not be delivered to its worker queue."""


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
    try:
        queue.enqueue(job.id)
    except Exception as exc:
        # A queued row without a broker message would look active forever.
        # Remove it so the caller can safely retry the action.
        try:
            session.delete(job)
            session.commit()
        except Exception:
            session.rollback()
        raise JobEnqueueError("Could not enqueue job") from exc
    return job


def load_job(session: Session, job_id: str) -> Job | None:
    return session.execute(select(Job).where(Job.id == job_id)).scalar_one_or_none()


def mark_running(job: Job) -> None:
    payload = dict(job.payload) if isinstance(job.payload, dict) else {}
    payload.pop("recording_phase", None)
    job.payload = payload
    job.status = "running"
    job.attempts += 1
    job.error = None
    job.started_at = utcnow()
    job.finished_at = None
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
    payload = dict(job.payload) if isinstance(job.payload, dict) else {}
    payload.pop("recording_phase", None)
    job.payload = payload
    job.status = "queued"
    job.error = error[:4000]
    job.started_at = None
    job.finished_at = None
    job.updated_at = utcnow()


def mark_recording_phase(job: Job, phase: str) -> None:
    if phase not in {"recording", "finalizing"}:
        raise ValueError("Unknown recording phase")
    payload = dict(job.payload) if isinstance(job.payload, dict) else {}
    payload["recording_phase"] = phase
    job.payload = payload
    job.updated_at = utcnow()


def iso_utc_now() -> str:
    return datetime.now(UTC).isoformat()
