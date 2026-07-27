from __future__ import annotations

from collections.abc import Generator
from typing import NoReturn

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from server_tg_home.core.config import Settings
from server_tg_home.database.session import new_session
from server_tg_home.webapp.auth import MiniAppAuthService
from server_tg_home.webapp.session import SessionRecord


SESSION_COOKIE_NAME = "sth_webapp_session"


async def authentication_not_configured() -> NoReturn:
    """Fail closed until the application supplies its Telegram auth dependency."""

    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Mini App authentication is not configured",
    )


async def require_webapp_session(request: Request) -> SessionRecord:
    auth_service = getattr(request.app.state, "webapp_auth", None)
    if not isinstance(auth_service, MiniAppAuthService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Mini App authentication is not configured",
        )

    authorization = request.headers.get("Authorization")
    if authorization:
        scheme, separator, value = authorization.partition(" ")
        if (
            separator != " "
            or scheme.lower() != "bearer"
            or not value
            or " " in value
        ):
            raise _unauthorized()
        token = value
    else:
        token = request.cookies.get(SESSION_COOKIE_NAME)

    session = await auth_service.authenticate(token)
    if session is None:
        raise _unauthorized()
    request.state.webapp_session = session
    request.state.webapp_session_token = token
    return session


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


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail={"code": "invalid_session", "message": "Mini App session is invalid or expired"},
        headers={"WWW-Authenticate": "Bearer"},
    )
