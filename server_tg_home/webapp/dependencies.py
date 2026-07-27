from __future__ import annotations

from collections.abc import Generator
from typing import NoReturn

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from server_tg_home.core.config import Settings
from server_tg_home.database.session import new_session


async def authentication_not_configured() -> NoReturn:
    """Fail closed until the application supplies its Telegram auth dependency."""

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Mini App authentication is not configured",
    )


def get_webapp_settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if not isinstance(settings, Settings):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Mini App settings are not available",
        )
    return settings


def get_webapp_session() -> Generator[Session, None, None]:
    session = new_session()
    try:
        yield session
    finally:
        session.close()
