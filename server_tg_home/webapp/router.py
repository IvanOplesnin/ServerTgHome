from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from server_tg_home.core.config import Settings
from server_tg_home.webapp.cameras import get_cameras, visible_camera_ids
from server_tg_home.webapp.climate import (
    DEFAULT_HISTORY_POINT_LIMIT,
    MAX_HISTORY_POINT_LIMIT,
    MIN_HISTORY_POINT_LIMIT,
    InvalidClimateHistory,
    UnknownClimateRoom,
    get_climate_history,
    get_current_climate,
)
from server_tg_home.webapp.dependencies import (
    authentication_not_configured,
    get_webapp_session,
    get_webapp_settings,
)
from server_tg_home.webapp.schemas import (
    CameraList,
    ClimateCurrent,
    ClimateHistory,
    VideoPage,
)
from server_tg_home.webapp.videos import (
    MAX_VIDEO_PAGE_SIZE,
    InvalidVideoCursor,
    VideoFile,
    VideoFileUnavailable,
    VideoNotFound,
    VideoRepository,
)

DependencyCallable = Callable[..., Any]


def create_webapp_router(
    *,
    auth_dependency: DependencyCallable = authentication_not_configured,
    session_dependency: DependencyCallable = get_webapp_session,
    settings_dependency: DependencyCallable = get_webapp_settings,
) -> APIRouter:
    """Create a read-only router with replaceable authentication and DB dependencies."""

    router = APIRouter(
        prefix="/api/webapp/v1",
        tags=["telegram-mini-app"],
        dependencies=[Depends(auth_dependency)],
    )

    @router.get("/cameras", response_model=CameraList)
    def cameras(
        settings: Settings = Depends(settings_dependency),
    ) -> CameraList:
        return get_cameras(settings)

    @router.get("/videos", response_model=VideoPage)
    def videos(
        settings: Settings = Depends(settings_dependency),
        session: Session = Depends(session_dependency),
        camera_id: str | None = Query(default=None, min_length=1, max_length=128),
        cursor: str | None = Query(default=None, min_length=1, max_length=512),
        limit: int = Query(default=30, ge=1, le=MAX_VIDEO_PAGE_SIZE),
    ) -> VideoPage:
        repository = VideoRepository(session, settings.storage.path)
        try:
            return repository.list_videos(
                camera_ids=visible_camera_ids(settings),
                camera_id=camera_id,
                cursor=cursor,
                limit=limit,
            )
        except InvalidVideoCursor as exc:
            raise _http_error(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "invalid_cursor",
                str(exc),
            ) from exc
        except VideoNotFound as exc:
            raise _http_error(
                status.HTTP_404_NOT_FOUND,
                "camera_not_found",
                str(exc),
            ) from exc

    @router.get("/videos/{video_id}/content", response_class=FileResponse)
    @router.head("/videos/{video_id}/content", response_class=FileResponse)
    def video_content(
        video_id: int = Path(ge=1),
        settings: Settings = Depends(settings_dependency),
        session: Session = Depends(session_dependency),
    ) -> FileResponse:
        artifact = _get_video_artifact(
            video_id,
            settings=settings,
            session=session,
        )
        return FileResponse(
            path=artifact.path,
            filename=artifact.filename,
            media_type=artifact.media_type,
            stat_result=artifact.stat_result,
            content_disposition_type="inline",
            headers={"Cache-Control": "private, no-store"},
        )

    @router.get("/videos/{video_id}/download", response_class=FileResponse)
    @router.head("/videos/{video_id}/download", response_class=FileResponse)
    def download_video(
        video_id: int = Path(ge=1),
        settings: Settings = Depends(settings_dependency),
        session: Session = Depends(session_dependency),
    ) -> FileResponse:
        artifact = _get_video_artifact(
            video_id,
            settings=settings,
            session=session,
        )
        return FileResponse(
            path=artifact.path,
            filename=artifact.filename,
            media_type=artifact.media_type,
            stat_result=artifact.stat_result,
            content_disposition_type="attachment",
            headers={"Cache-Control": "private, no-store"},
        )

    @router.get("/climate/current", response_model=ClimateCurrent)
    def climate_current(
        settings: Settings = Depends(settings_dependency),
        session: Session = Depends(session_dependency),
    ) -> ClimateCurrent:
        return get_current_climate(settings, session)

    @router.get("/climate/history", response_model=ClimateHistory)
    def climate_history(
        room_id: str = Query(min_length=1, max_length=128),
        settings: Settings = Depends(settings_dependency),
        session: Session = Depends(session_dependency),
        from_: datetime | None = Query(default=None, alias="from"),
        to: datetime | None = Query(default=None),
        bucket: str = Query(default="auto", min_length=2, max_length=16),
        point_limit: int = Query(
            default=DEFAULT_HISTORY_POINT_LIMIT,
            ge=MIN_HISTORY_POINT_LIMIT,
            le=MAX_HISTORY_POINT_LIMIT,
        ),
    ) -> ClimateHistory:
        try:
            return get_climate_history(
                settings,
                session,
                room_id=room_id,
                from_=from_,
                to=to,
                bucket=bucket,
                point_limit=point_limit,
            )
        except UnknownClimateRoom as exc:
            raise _http_error(
                status.HTTP_404_NOT_FOUND,
                "room_not_found",
                str(exc),
            ) from exc
        except InvalidClimateHistory as exc:
            raise _http_error(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "invalid_history_query",
                str(exc),
            ) from exc

    return router


def _get_video_artifact(
    video_id: int,
    *,
    settings: Settings,
    session: Session,
) -> VideoFile:
    repository = VideoRepository(session, settings.storage.path)
    try:
        return repository.get_video_file(
            video_id,
            camera_ids=visible_camera_ids(settings),
        )
    except VideoNotFound as exc:
        raise _http_error(
            status.HTTP_404_NOT_FOUND,
            "video_not_found",
            str(exc),
        ) from exc
    except VideoFileUnavailable as exc:
        raise _http_error(
            status.HTTP_410_GONE,
            "video_file_unavailable",
            str(exc),
        ) from exc


def _http_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )
