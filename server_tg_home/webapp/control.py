from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import re
from typing import Annotated, cast
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, Response, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from server_tg_home.core.config import Settings
from server_tg_home.database.models import Job
from server_tg_home.jobs.factory import create_record_video_file_job
from server_tg_home.jobs.queue import JobQueue
from server_tg_home.jobs.repository import JobEnqueueError
from server_tg_home.jobs.recording_status import (
    MAX_RECORD_DURATION_SEC,
    list_active_recordings,
    list_recent_recording_results,
)
from server_tg_home.webapp.auth import (
    MiniAppAccessDeniedError,
    MiniAppAuthService,
    TelegramInitDataError,
)
from server_tg_home.webapp.cameras import get_cameras, visible_camera_ids
from server_tg_home.webapp.dependencies import (
    SESSION_COOKIE_NAME,
    get_webapp_session,
    get_webapp_settings,
    require_webapp_session,
)
from server_tg_home.webapp.schemas import (
    BootstrapResponse,
    BootstrapTab,
    ClimateRoomDefinition,
    RecordingActivityItem,
    RecordingActivityList,
    RecordingResultItem,
    SessionLoginRequest,
    SessionResponse,
    StartRecordingRequest,
    StartRecordingResponse,
    StreamTicketResponse,
    VideoTicketResponse,
    WebAppUser,
)
from server_tg_home.webapp.session import SessionRecord
from server_tg_home.webapp.tickets import RedisTicketStore, TicketPrincipal
from server_tg_home.webapp.videos import (
    VideoFile,
    VideoFileUnavailable,
    VideoNotFound,
    VideoRepository,
)

MEDIA_ROUTE_PATTERN = re.compile(
    r"^/media/t/(?P<token>[A-Za-z0-9_-]{32,256})/api/"
    r"(?P<endpoint>"
    r"ws|stream\.m3u8|"
    r"hls/(?:playlist\.m3u8|segment\.ts|init\.mp4|segment\.m4s)"
    r")$"
)


def create_webapp_control_router() -> APIRouter:
    router = APIRouter(prefix="/api/webapp/v1", tags=["telegram-mini-app"])

    @router.post("/session", response_model=SessionResponse)
    async def create_session(
        body: SessionLoginRequest,
        request: Request,
        response: Response,
    ) -> SessionResponse:
        auth_service = _auth_service(request)
        try:
            created = await auth_service.login(body.init_data)
        except TelegramInitDataError as exc:
            raise _http_error(
                status.HTTP_401_UNAUTHORIZED,
                "invalid_init_data",
                "Telegram authentication data is invalid or expired",
            ) from exc
        except MiniAppAccessDeniedError as exc:
            raise _http_error(
                status.HTTP_403_FORBIDDEN,
                "access_denied",
                "This Telegram user is not allowed to open the Mini App",
            ) from exc

        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=created.token,
            max_age=max(1, created.session.expires_at - created.session.created_at),
            expires=_as_datetime(created.session.expires_at),
            path="/api/webapp/v1",
            secure=True,
            httponly=True,
            samesite="none",
        )
        response.headers["Cache-Control"] = "no-store"
        return SessionResponse(
            access_token=created.token,
            expires_at=_as_datetime(created.session.expires_at),
            user=_webapp_user(created.session),
        )

    @router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_session(
        request: Request,
        principal: Annotated[SessionRecord, Depends(require_webapp_session)],
    ) -> Response:
        del principal
        token = getattr(request.state, "webapp_session_token", None)
        await _auth_service(request).logout(token)
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.delete_cookie(
            key=SESSION_COOKIE_NAME,
            path="/api/webapp/v1",
            secure=True,
            httponly=True,
            samesite="none",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @router.get("/bootstrap", response_model=BootstrapResponse)
    def bootstrap(
        principal: Annotated[SessionRecord, Depends(require_webapp_session)],
        settings: Annotated[Settings, Depends(get_webapp_settings)],
    ) -> BootstrapResponse:
        tabs = [
            BootstrapTab(**tab.model_dump())
            for tab in settings.webapp.tabs
            if tab.enabled
            and (
                tab.required_role == "viewer"
                or principal.role == "admin"
            )
        ]
        climate_rooms = [
            ClimateRoomDefinition(id=room_id, title=room.title)
            for room_id, room in settings.temperatures.rooms.items()
        ]
        return BootstrapResponse(
            user=_webapp_user(principal),
            tabs=tabs,
            cameras=get_cameras(settings).items,
            climate_rooms=climate_rooms,
        )

    @router.get(
        "/recordings",
        response_model=RecordingActivityList,
    )
    async def active_recordings(
        response: Response,
        principal: Annotated[SessionRecord, Depends(require_webapp_session)],
        settings: Annotated[Settings, Depends(get_webapp_settings)],
        session: Annotated[Session, Depends(get_webapp_session)],
    ) -> RecordingActivityList:
        _require_admin(principal)
        response.headers["Cache-Control"] = "private, no-store"
        now = datetime.now(UTC)
        activities = list_active_recordings(
            session,
            camera_ids=set(visible_camera_ids(settings)),
            now=now,
        )
        recent_results = list_recent_recording_results(
            session,
            camera_ids=set(visible_camera_ids(settings)),
            now=now,
        )
        return RecordingActivityList(
            items=[
                RecordingActivityItem(
                    job_id=activity.job_id,
                    camera_id=activity.camera_id,
                    status=activity.status,
                    phase=activity.phase,
                    duration_sec=activity.duration_sec,
                    created_at=activity.created_at,
                    started_at=activity.started_at,
                    expected_finish_at=activity.expected_finish_at,
                )
                for activity in activities
            ],
            recent_results=[
                RecordingResultItem(
                    job_id=result.job_id,
                    camera_id=result.camera_id,
                    status=result.status,
                    finished_at=result.finished_at,
                    video_id=result.video_id,
                )
                for result in recent_results
            ],
            generated_at=now,
        )

    @router.post(
        "/cameras/{camera_id}/recordings",
        response_model=StartRecordingResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def start_recording(
        camera_id: Annotated[str, Path(min_length=1, max_length=128)],
        body: StartRecordingRequest,
        request: Request,
        response: Response,
        principal: Annotated[SessionRecord, Depends(require_webapp_session)],
        settings: Annotated[Settings, Depends(get_webapp_settings)],
        session: Annotated[Session, Depends(get_webapp_session)],
    ) -> StartRecordingResponse:
        _require_admin(principal)
        camera = settings.cameras.get(camera_id)
        if camera is None or not camera.web_enabled:
            raise _http_error(
                status.HTTP_404_NOT_FOUND,
                "camera_not_found",
                "The requested camera is unavailable",
            )

        response.headers["Cache-Control"] = "private, no-store"
        duration_sec = (
            body.duration_sec
            if body.duration_sec is not None
            else camera.default_duration_sec
        )
        duration_sec = max(1, min(duration_sec, MAX_RECORD_DURATION_SEC))
        async with _camera_recording_lock(request, camera_id):
            already_active = any(
                activity.blocks_new_recording
                for activity in list_active_recordings(
                    session,
                    camera_ids={camera_id},
                )
            )
            if already_active:
                raise _http_error(
                    status.HTTP_409_CONFLICT,
                    "recording_already_active",
                    (
                        "Для этой камеры уже запущена запись "
                        "или есть запись в очереди"
                    ),
                )

            try:
                job_id = create_record_video_file_job(
                    settings,
                    session,
                    _job_queue(request),
                    source="telegram_mini_app",
                    camera_id=camera_id,
                    duration_sec=duration_sec,
                    pre_event_sec=0,
                    chat_ids=[],
                    message_thread_id=None,
                    message=f"Mini App recording {camera_id}",
                    use_default_chat_ids=False,
                    requested_by_user_id=principal.user_id,
                )
            except JobEnqueueError as exc:
                raise _http_error(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    "recording_queue_unavailable",
                    (
                        "Не удалось поставить запись в очередь. "
                        "Повторите позже."
                    ),
                ) from exc

        job = session.get(Job, job_id)
        if job is None:
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "recording_job_unavailable",
                "Не удалось получить созданное задание записи.",
            )
        return StartRecordingResponse(
            job_id=job_id,
            camera_id=camera_id,
            duration_sec=duration_sec,
            created_at=_as_utc(job.created_at),
        )

    @router.post(
        "/streams/{camera_id}/ticket",
        response_model=StreamTicketResponse,
    )
    async def create_stream_ticket(
        camera_id: Annotated[str, Path(min_length=1, max_length=128)],
        request: Request,
        principal: Annotated[SessionRecord, Depends(require_webapp_session)],
        settings: Annotated[Settings, Depends(get_webapp_settings)],
    ) -> StreamTicketResponse:
        camera = settings.cameras.get(camera_id)
        if (
            camera is None
            or not camera.web_enabled
            or not camera.go2rtc_stream
        ):
            raise _http_error(
                status.HTTP_404_NOT_FOUND,
                "camera_not_found",
                "The requested live camera is unavailable",
            )

        created = await _ticket_store(request).create(
            TicketPrincipal(
                purpose="stream",
                user_id=principal.user_id,
                resource_id=camera_id,
            ),
            ttl_sec=settings.webapp.media_ticket_ttl_sec,
        )
        origin = _public_origin(settings)
        ticket_path = quote(created.token, safe="")
        query = urlencode({"src": camera.go2rtc_stream})
        ws_origin = (
            f"wss://{urlsplit(origin).netloc}"
            if origin.startswith("https://")
            else f"ws://{urlsplit(origin).netloc}"
        )
        media_path = f"/media/t/{ticket_path}/api"
        return StreamTicketResponse(
            ws_url=f"{ws_origin}{media_path}/ws?{query}",
            hls_url=f"{origin}{media_path}/stream.m3u8?{query}",
            expires_at=_as_datetime(created.ticket.expires_at),
        )

    @router.post(
        "/videos/{video_id}/download-ticket",
        response_model=VideoTicketResponse,
    )
    async def create_video_ticket(
        video_id: Annotated[int, Path(ge=1)],
        request: Request,
        principal: Annotated[SessionRecord, Depends(require_webapp_session)],
        settings: Annotated[Settings, Depends(get_webapp_settings)],
        session: Annotated[Session, Depends(get_webapp_session)],
    ) -> VideoTicketResponse:
        artifact = _video_artifact(video_id, settings=settings, session=session)
        created = await _ticket_store(request).create(
            TicketPrincipal(
                purpose="download",
                user_id=principal.user_id,
                resource_id=str(video_id),
            ),
            ttl_sec=settings.webapp.video_ticket_ttl_sec,
        )
        token = quote(created.token, safe="")
        prefix = f"/api/webapp/v1/files/{token}"
        return VideoTicketResponse(
            url=f"{prefix}/download",
            content_url=f"{prefix}/content",
            filename=artifact.filename,
            expires_at=_as_datetime(created.ticket.expires_at),
        )

    @router.get("/files/{ticket_token}/content", response_class=FileResponse)
    @router.head("/files/{ticket_token}/content", response_class=FileResponse)
    async def video_ticket_content(
        ticket_token: Annotated[str, Path(min_length=32, max_length=256)],
        request: Request,
        settings: Annotated[Settings, Depends(get_webapp_settings)],
        session: Annotated[Session, Depends(get_webapp_session)],
    ) -> FileResponse:
        artifact = await _ticket_video_artifact(
            ticket_token,
            request=request,
            settings=settings,
            session=session,
        )
        return _video_response(artifact, disposition="inline")

    @router.get("/files/{ticket_token}/download", response_class=FileResponse)
    @router.head("/files/{ticket_token}/download", response_class=FileResponse)
    async def video_ticket_download(
        ticket_token: Annotated[str, Path(min_length=32, max_length=256)],
        request: Request,
        settings: Annotated[Settings, Depends(get_webapp_settings)],
        session: Annotated[Session, Depends(get_webapp_session)],
    ) -> FileResponse:
        artifact = await _ticket_video_artifact(
            ticket_token,
            request=request,
            settings=settings,
            session=session,
        )
        return _video_response(artifact, disposition="attachment")

    @router.get("/media/authorize", status_code=status.HTTP_204_NO_CONTENT)
    async def authorize_media(
        request: Request,
        settings: Annotated[Settings, Depends(get_webapp_settings)],
        forwarded_uri: Annotated[
            str | None,
            Header(alias="X-Forwarded-Uri"),
        ] = None,
        forwarded_method: Annotated[
            str | None,
            Header(alias="X-Forwarded-Method"),
        ] = None,
    ) -> Response:
        if (forwarded_method or "").upper() not in {"GET", "HEAD"}:
            raise _media_denied()

        parsed = urlsplit(forwarded_uri or "")
        match = MEDIA_ROUTE_PATTERN.fullmatch(parsed.path)
        if match is None:
            raise _media_denied()

        ticket = await _ticket_store(request).get(
            match.group("token"),
            purpose="stream",
        )
        if (
            ticket is None
            or not await _auth_service(request).user_is_active(ticket.user_id)
        ):
            raise _media_denied()

        camera = settings.cameras.get(ticket.resource_id)
        if (
            camera is None
            or not camera.web_enabled
            or not camera.go2rtc_stream
        ):
            raise _media_denied()

        endpoint = match.group("endpoint")
        if endpoint in {"ws", "stream.m3u8"}:
            try:
                query = parse_qs(
                    parsed.query,
                    keep_blank_values=True,
                    strict_parsing=True,
                    max_num_fields=20,
                )
            except ValueError as exc:
                raise _media_denied() from exc
            sources = query.get("src", [])
            if sources != [camera.go2rtc_stream]:
                raise _media_denied()

        return Response(
            status_code=status.HTTP_204_NO_CONTENT,
            headers={"Cache-Control": "private, no-store"},
        )

    return router


def _auth_service(request: Request) -> MiniAppAuthService:
    service = getattr(request.app.state, "webapp_auth", None)
    if not isinstance(service, MiniAppAuthService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Mini App authentication is not configured",
        )
    return service


def _ticket_store(request: Request) -> RedisTicketStore:
    store = getattr(request.app.state, "webapp_tickets", None)
    if not isinstance(store, RedisTicketStore):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Mini App ticket storage is not configured",
        )
    return store


def _job_queue(request: Request) -> JobQueue:
    queue = getattr(request.app.state, "queue", None)
    if queue is None or not callable(getattr(queue, "enqueue", None)):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Job queue is not configured",
        )
    return cast(JobQueue, queue)


def _camera_recording_lock(
    request: Request,
    camera_id: str,
) -> asyncio.Lock:
    locks = getattr(request.app.state, "webapp_recording_locks", None)
    if not isinstance(locks, dict):
        locks = {}
        request.app.state.webapp_recording_locks = locks
    lock = locks.get(camera_id)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        locks[camera_id] = lock
    return lock


def _require_admin(principal: SessionRecord) -> None:
    if not principal.is_admin:
        raise _http_error(
            status.HTTP_403_FORBIDDEN,
            "admin_required",
            "Only administrators can manage camera recordings",
        )


def _webapp_user(principal: SessionRecord) -> WebAppUser:
    return WebAppUser(
        id=principal.user_id,
        first_name=principal.first_name,
        last_name=principal.last_name,
        username=principal.username,
        role=principal.role,
        is_admin=principal.is_admin,
    )


def _public_origin(settings: Settings) -> str:
    public_url = settings.webapp.public_url
    if not public_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Mini App public URL is not configured",
        )
    parsed = urlsplit(public_url)
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


async def _ticket_video_artifact(
    token: str,
    *,
    request: Request,
    settings: Settings,
    session: Session,
) -> VideoFile:
    ticket = await _ticket_store(request).get(token, purpose="download")
    if (
        ticket is None
        or not await _auth_service(request).user_is_active(ticket.user_id)
        or not ticket.resource_id.isdecimal()
    ):
        raise _ticket_not_found()
    video_id = int(ticket.resource_id)
    if video_id <= 0:
        raise _ticket_not_found()
    try:
        return _video_artifact(video_id, settings=settings, session=session)
    except HTTPException as exc:
        raise _ticket_not_found() from exc


def _video_artifact(
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
            "The requested video was not found",
        ) from exc
    except VideoFileUnavailable as exc:
        raise _http_error(
            status.HTTP_410_GONE,
            "video_file_unavailable",
            "The requested video file is unavailable",
        ) from exc


def _video_response(
    artifact: VideoFile,
    *,
    disposition: str,
) -> FileResponse:
    return FileResponse(
        path=artifact.path,
        filename=artifact.filename,
        media_type=artifact.media_type,
        stat_result=artifact.stat_result,
        content_disposition_type=disposition,
        headers={"Cache-Control": "private, no-store"},
    )


def _as_datetime(timestamp: int) -> datetime:
    return datetime.fromtimestamp(timestamp, UTC)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _ticket_not_found() -> HTTPException:
    return _http_error(
        status.HTTP_404_NOT_FOUND,
        "ticket_not_found",
        "The requested link is invalid or expired",
    )


def _media_denied() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Media access denied",
    )


def _http_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message},
    )
