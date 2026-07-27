from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import stat
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from server_tg_home.database.models import Video
from server_tg_home.media.storage import VIDEO_EXTENSIONS
from server_tg_home.webapp.schemas import VideoItem, VideoPage

VIDEO_API_PREFIX = "/api/webapp/v1/videos"
MAX_VIDEO_PAGE_SIZE = 100
MAX_VIDEO_ROWS_SCANNED_PER_PAGE = 5_000
VIDEO_SCAN_BATCH_SIZE = 200


class InvalidVideoCursor(ValueError):
    pass


class VideoNotFound(LookupError):
    pass


class VideoFileUnavailable(LookupError):
    pass


@dataclass(frozen=True)
class VideoFile:
    path: Path
    stat_result: os.stat_result
    filename: str
    media_type: str


@dataclass(frozen=True)
class _Cursor:
    created_at: datetime
    video_id: int
    camera_id: str | None


class VideoRepository:
    def __init__(self, session: Session, storage_root: Path) -> None:
        self._session = session
        self._storage_root = storage_root.resolve(strict=False)

    def list_videos(
        self,
        *,
        camera_ids: list[str],
        camera_id: str | None = None,
        cursor: str | None = None,
        limit: int = 30,
    ) -> VideoPage:
        if not 1 <= limit <= MAX_VIDEO_PAGE_SIZE:
            raise ValueError(f"limit must be between 1 and {MAX_VIDEO_PAGE_SIZE}")
        if camera_id is not None and camera_id not in camera_ids:
            raise VideoNotFound(f"Unknown camera: {camera_id}")

        position = decode_video_cursor(cursor) if cursor else None
        if position is not None and position.camera_id != camera_id:
            raise InvalidVideoCursor("cursor does not belong to this camera filter")

        found: list[tuple[VideoItem, _Cursor]] = []
        last_scanned: _Cursor | None = None
        scanned = 0
        exhausted = False

        while len(found) <= limit and scanned < MAX_VIDEO_ROWS_SCANNED_PER_PAGE:
            batch_size = min(
                VIDEO_SCAN_BATCH_SIZE,
                MAX_VIDEO_ROWS_SCANNED_PER_PAGE - scanned,
            )
            rows = self._load_batch(
                camera_ids=camera_ids,
                camera_id=camera_id,
                position=position,
                limit=batch_size,
            )
            if not rows:
                exhausted = True
                break

            scanned += len(rows)
            for row in rows:
                row_position = _Cursor(
                    created_at=_as_utc(row.created_at),
                    video_id=row.id,
                    camera_id=camera_id,
                )
                last_scanned = row_position
                position = row_position
                artifact = self._resolve_stored_file(row)
                if artifact is None:
                    continue
                found.append(
                    (
                        _video_item(row, size_bytes=artifact.stat_result.st_size),
                        row_position,
                    )
                )
                if len(found) > limit:
                    break

            if len(found) > limit:
                break
            if len(rows) < batch_size:
                exhausted = True
                break

        page_items = [item for item, _ in found[:limit]]
        next_position: _Cursor | None = None
        if len(found) > limit and page_items:
            next_position = found[limit - 1][1]
        elif not exhausted and last_scanned is not None:
            next_position = last_scanned

        return VideoPage(
            items=page_items,
            next_cursor=encode_video_cursor(next_position) if next_position else None,
        )

    def get_video_file(self, video_id: int, *, camera_ids: list[str]) -> VideoFile:
        row = self._session.execute(
            select(Video).where(
                Video.id == video_id,
                Video.deleted_at.is_(None),
                Video.camera_id.in_(camera_ids),
            )
        ).scalar_one_or_none()
        if row is None:
            raise VideoNotFound(f"Video {video_id} was not found")

        artifact = self._resolve_stored_file(row)
        if artifact is None:
            raise VideoFileUnavailable(f"File for video {video_id} is unavailable")
        return artifact

    def _load_batch(
        self,
        *,
        camera_ids: list[str],
        camera_id: str | None,
        position: _Cursor | None,
        limit: int,
    ) -> list[Video]:
        query = select(Video).where(
            Video.deleted_at.is_(None),
            Video.camera_id.in_(camera_ids),
        )
        if camera_id is not None:
            query = query.where(Video.camera_id == camera_id)
        if position is not None:
            query = query.where(
                or_(
                    Video.created_at < position.created_at,
                    and_(
                        Video.created_at == position.created_at,
                        Video.id < position.video_id,
                    ),
                )
            )
        query = query.order_by(Video.created_at.desc(), Video.id.desc()).limit(limit)
        return list(self._session.execute(query).scalars())

    def _resolve_stored_file(self, row: Video) -> VideoFile | None:
        stored_path = Path(row.path)
        candidate = stored_path if stored_path.is_absolute() else Path.cwd() / stored_path
        try:
            resolved = candidate.resolve(strict=True)
            if not resolved.is_relative_to(self._storage_root):
                return None
            stat_result = resolved.stat()
        except (FileNotFoundError, OSError, RuntimeError):
            return None

        if not stat.S_ISREG(stat_result.st_mode):
            return None
        suffix = resolved.suffix.lower()
        if suffix not in VIDEO_EXTENSIONS:
            return None
        return VideoFile(
            path=resolved,
            stat_result=stat_result,
            filename=f"video-{row.id}{suffix}",
            media_type=_video_media_type(suffix),
        )


def encode_video_cursor(cursor: _Cursor) -> str:
    payload = {
        "v": 1,
        "created_at": _as_utc(cursor.created_at).isoformat(),
        "id": cursor.video_id,
        "camera_id": cursor.camera_id,
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")


def decode_video_cursor(value: str) -> _Cursor:
    if not value or len(value) > 512:
        raise InvalidVideoCursor("invalid cursor")
    try:
        padded = value + "=" * (-len(value) % 4)
        payload: Any = json.loads(
            base64.b64decode(padded, altchars=b"-_", validate=True).decode("utf-8")
        )
        if not isinstance(payload, dict) or payload.get("v") != 1:
            raise InvalidVideoCursor("unsupported cursor")
        created_at = datetime.fromisoformat(str(payload["created_at"]))
        video_id = int(payload["id"])
        camera_id_value = payload.get("camera_id")
        if camera_id_value is not None and not isinstance(camera_id_value, str):
            raise InvalidVideoCursor("invalid camera cursor")
    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        if isinstance(exc, InvalidVideoCursor):
            raise
        raise InvalidVideoCursor("invalid cursor") from exc

    if video_id <= 0:
        raise InvalidVideoCursor("invalid video cursor")
    return _Cursor(
        created_at=_as_utc(created_at),
        video_id=video_id,
        camera_id=camera_id_value,
    )


def _video_item(row: Video, *, size_bytes: int) -> VideoItem:
    return VideoItem(
        id=row.id,
        camera_id=row.camera_id,
        size_bytes=size_bytes,
        duration_sec=row.duration_sec,
        created_at=_as_utc(row.created_at),
        content_url=f"{VIDEO_API_PREFIX}/{row.id}/content",
        download_url=f"{VIDEO_API_PREFIX}/{row.id}/download",
    )


def _video_media_type(suffix: str) -> str:
    return {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".mkv": "video/x-matroska",
        ".avi": "video/x-msvideo",
    }.get(suffix, "application/octet-stream")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
