from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


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
