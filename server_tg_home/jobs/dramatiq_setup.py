from __future__ import annotations

import dramatiq
from dramatiq.brokers.redis import RedisBroker

from server_tg_home.core.config import Settings

_configured_redis_url: str | None = None


def configure_dramatiq_broker(settings: Settings) -> RedisBroker:
    global _configured_redis_url

    current_broker = dramatiq.get_broker()
    if isinstance(current_broker, RedisBroker) and _configured_redis_url == settings.app.redis_url:
        return current_broker

    broker = RedisBroker(url=settings.app.redis_url)
    dramatiq.set_broker(broker)
    _configured_redis_url = settings.app.redis_url
    return broker
