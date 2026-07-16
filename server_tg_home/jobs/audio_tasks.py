from __future__ import annotations

import logging

import dramatiq

from server_tg_home.core.config import load_settings
from server_tg_home.core.logging import configure_logging
from server_tg_home.database.session import init_db
from server_tg_home.jobs.dramatiq_setup import configure_dramatiq_broker
from server_tg_home.jobs.processor import JobProcessor
from server_tg_home.media.storage import ensure_storage

settings = load_settings()
configure_logging(settings.app.log_level)
configure_dramatiq_broker(settings)

logger = logging.getLogger(__name__)


@dramatiq.actor(queue_name=settings.audio.queue_name, max_retries=0)
def process_audio_job(job_id: str) -> None:
    init_db(settings.app.database_url)
    ensure_storage(settings)
    processor = JobProcessor(settings, retry_callback=enqueue_audio_job)
    processor.process_job_id(job_id)


def enqueue_audio_job(job_id: str, delay_ms: int = 0) -> None:
    if delay_ms > 0:
        process_audio_job.send_with_options(args=(job_id,), delay=delay_ms)
    else:
        process_audio_job.send(job_id)
