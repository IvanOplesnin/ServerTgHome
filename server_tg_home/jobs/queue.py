from __future__ import annotations

import logging
from collections.abc import Callable

from redis import Redis

from server_tg_home.core.config import Settings
from server_tg_home.jobs.dramatiq_setup import configure_dramatiq_broker

logger = logging.getLogger(__name__)


class JobQueue:
    def __init__(
        self,
        settings: Settings,
        *,
        queue_name: str | None = None,
        enqueue_func: Callable[[str], None] | None = None,
    ) -> None:
        self.settings = settings
        self.redis = Redis.from_url(settings.app.redis_url, decode_responses=True)
        self.broker = configure_dramatiq_broker(settings)
        self.queue_name = queue_name or settings.app.queue_name
        self._enqueue_func = enqueue_func
        self.broker.declare_queue(self.queue_name)

    def enqueue(self, job_id: str) -> None:
        if self._enqueue_func is not None:
            self._enqueue_func(job_id)
            return
        from .tasks import enqueue_job

        enqueue_job(job_id)

    def length(self) -> int:
        try:
            return int(
                self.redis.llen(f"dramatiq:{self.queue_name}")
                + self.redis.llen(f"dramatiq:{self.queue_name}.DQ")
            )
        except Exception:
            logger.debug("Failed to read Dramatiq queue length", exc_info=True)
            return 0

    def ping(self) -> bool:
        return bool(self.redis.ping())
