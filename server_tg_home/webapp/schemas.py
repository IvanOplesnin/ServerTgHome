from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SessionLoginRequest(BaseModel):
    init_data: str = Field(min_length=1, max_length=16_384)


class WebAppUser(BaseModel):
    id: int
    first_name: str
    last_name: str | None = None
    username: str | None = None
    role: Literal["admin", "viewer"]
    is_admin: bool


class SessionResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: WebAppUser


class BootstrapTab(BaseModel):
    id: str
    title: str
    kind: str
    enabled: bool
    required_role: Literal["admin", "viewer"]


class ClimateRoomDefinition(BaseModel):
    id: str
    title: str


class BootstrapResponse(BaseModel):
    user: WebAppUser
    tabs: list[BootstrapTab]
    cameras: list["CameraItem"]
    climate_rooms: list[ClimateRoomDefinition]


class StreamTicketResponse(BaseModel):
    ws_url: str
    hls_url: str
    player_script_url: str = "/media/video-stream.js"
    modes: list[str] = Field(
        default_factory=lambda: ["webrtc", "mse", "hls"]
    )
    media: str = "video,audio"
    expires_at: datetime


class VideoTicketResponse(BaseModel):
    url: str
    content_url: str
    filename: str
    expires_at: datetime


class StartRecordingRequest(BaseModel):
    duration_sec: int | None = Field(
        default=None,
        ge=1,
        le=3600,
        strict=True,
    )


class StartRecordingResponse(BaseModel):
    job_id: str
    camera_id: str
    duration_sec: int
    status: Literal["queued"] = "queued"
    phase: Literal["queued"] = "queued"
    created_at: datetime


class RecordingActivityItem(BaseModel):
    job_id: str
    camera_id: str
    status: Literal["queued", "running"]
    phase: Literal["queued", "recording", "finalizing", "stale"]
    duration_sec: int
    created_at: datetime
    started_at: datetime | None
    expected_finish_at: datetime | None


class RecordingResultItem(BaseModel):
    job_id: str
    camera_id: str
    status: Literal["done", "failed"]
    finished_at: datetime
    video_id: int | None


class RecordingActivityList(BaseModel):
    items: list[RecordingActivityItem]
    recent_results: list[RecordingResultItem]
    generated_at: datetime


class VideoItem(BaseModel):
    id: int
    camera_id: str
    size_bytes: int
    duration_sec: int | None
    created_at: datetime
    content_url: str
    download_url: str


class VideoPage(BaseModel):
    items: list[VideoItem]
    next_cursor: str | None = None


class CameraHealthItem(BaseModel):
    state: str
    available: bool
    reason: str
    last_segment_at: datetime | None = None
    last_segment_age_sec: int | None = None


class CameraItem(BaseModel):
    id: str
    title: str
    live_available: bool
    health: CameraHealthItem


class CameraList(BaseModel):
    items: list[CameraItem]


class ClimateReading(BaseModel):
    value: float
    unit: str
    updated_at: datetime
    stale: bool


class ClimateRoom(BaseModel):
    id: str
    title: str
    temperature: ClimateReading | None
    humidity: ClimateReading | None


class ClimateCurrent(BaseModel):
    rooms: list[ClimateRoom]
    generated_at: datetime


class ClimateHistoryPoint(BaseModel):
    timestamp: datetime
    value: float
    sample_count: int = Field(ge=1)


class ClimateHistorySeries(BaseModel):
    metric: Literal["temperature", "humidity"]
    unit: str
    points: list[ClimateHistoryPoint]


class ClimateHistory(BaseModel):
    room_id: str
    from_: datetime = Field(serialization_alias="from")
    to: datetime
    bucket_sec: int = Field(ge=1)
    series: list[ClimateHistorySeries]
