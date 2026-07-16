from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_CONFIG_PATH = "config/config.yaml"
ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")


class AppConfig(BaseModel):
    database_url: str = (
        "postgresql+psycopg://server_tg_home:server_tg_home_password"
        "@localhost:5432/server_tg_home"
    )
    redis_url: str = "redis://localhost:6379/0"
    queue_name: str = "server_tg_home_jobs"
    log_level: str = "INFO"
    webhook_token: str | None = None
    max_job_attempts: int = 2


class ApiConfig(BaseModel):
    enable_telegram_polling: bool = True


class TelegramPanelConfig(BaseModel):
    title: str
    kind: Literal["door", "climate"]
    chat_id: int | None = None
    message_thread_id: int | None = None
    camera_id: str | None = None
    room_id: str = "all"
    video_duration_sec: int = 20


class TelegramConfig(BaseModel):
    bot_token: str | None = None
    proxy_url: str | None = None
    allowed_chat_ids: list[int] = Field(default_factory=list)
    default_chat_ids: list[int] = Field(default_factory=list)
    default_message_thread_id: int | None = None
    admin_user_ids: list[int] = Field(default_factory=list)
    panels: dict[str, TelegramPanelConfig] = Field(default_factory=dict)
    request_timeout_sec: int = 180
    polling_timeout_sec: int = 30


class HomeAssistantConfig(BaseModel):
    base_url: str | None = None
    token: str | None = None
    request_timeout_sec: int = 20


class TemperatureRoomConfig(BaseModel):
    title: str


class TemperaturesConfig(BaseModel):
    rooms: dict[str, TemperatureRoomConfig] = Field(
        default_factory=lambda: {
            "bedroom": TemperatureRoomConfig(title="Спальня"),
            "living_room": TemperatureRoomConfig(title="Гостиная"),
        }
    )
    default_unit: str = "°C"
    default_humidity_unit: str = "%"
    stale_after_sec: int = 7200


class GraphsConfig(BaseModel):
    queue_name: str = "server_tg_home_graph_jobs"
    path: Path = Path("./data/graphs")
    default_window: str = "24h"
    max_window: str = "30d"
    width: int = 1200
    height_per_panel: int = 360
    scale: int = 2
    history_retention_days: int = 180
    artifact_retention_days: int = 14


class CameraHealthConfig(BaseModel):
    enabled: bool = True
    poll_sec: int = 60
    stale_after_sec: int | None = None
    startup_grace_sec: int = 120
    notify_recovery: bool = True
    notify_chat_ids: list[int] = Field(default_factory=list)
    notify_message_thread_id: int | None = None


class StorageConfig(BaseModel):
    path: Path = Path("./data/clips")
    max_size_mb: int = 10_240
    warning_threshold_percent: int = 85
    cleanup_target_percent: int = 75
    delete_batch_size: int = 10
    warning_cooldown_sec: int = 3600
    retention_poll_sec: int = 300
    notify_chat_ids: list[int] = Field(default_factory=list)
    notify_message_thread_id: int | None = None

    @property
    def max_size_bytes(self) -> int:
        return self.max_size_mb * 1024 * 1024

    @property
    def warning_threshold_bytes(self) -> int:
        return int(self.max_size_bytes * self.warning_threshold_percent / 100)

    @property
    def cleanup_target_bytes(self) -> int:
        return int(self.max_size_bytes * self.cleanup_target_percent / 100)


class BufferConfig(BaseModel):
    enabled: bool = True
    path: Path = Path("./data/buffer")
    pre_event_seconds: int = 4
    segment_seconds: int = 1
    keep_seconds: int = 60
    restart_delay_sec: int = 5


class CameraConfig(BaseModel):
    rtsp_url: str
    buffer_enabled: bool = True
    default_duration_sec: int = 20
    ffmpeg_input_args: list[str] = Field(default_factory=lambda: ["-rtsp_transport", "tcp"])
    ffmpeg_output_args: list[str] = Field(
        default_factory=lambda: [
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
        ]
    )
    ffmpeg_clip_output_args: list[str] = Field(
        default_factory=lambda: [
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
        ]
    )


class EventConfig(BaseModel):
    camera_id: str
    duration_sec: int = 20
    pre_event_sec: int | None = None
    cooldown_sec: int = 0
    dedupe_window_sec: int = 5
    chat_ids: list[int] = Field(default_factory=list)
    message_thread_id: int | None = None
    message: str | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app: AppConfig = Field(default_factory=AppConfig)
    api: ApiConfig = Field(default_factory=ApiConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)
    home_assistant: HomeAssistantConfig = Field(default_factory=HomeAssistantConfig)
    temperatures: TemperaturesConfig = Field(default_factory=TemperaturesConfig)
    graphs: GraphsConfig = Field(default_factory=GraphsConfig)
    camera_health: CameraHealthConfig = Field(default_factory=CameraHealthConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    buffer: BufferConfig = Field(default_factory=BufferConfig)
    cameras: dict[str, CameraConfig] = Field(default_factory=dict)
    events: dict[str, EventConfig] = Field(default_factory=dict)

    @field_validator("cameras")
    @classmethod
    def require_camera_ids(cls, value: dict[str, CameraConfig]) -> dict[str, CameraConfig]:
        return value


def _clean_empty_env_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _clean_empty_env_values(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean_empty_env_values(item) for item in value]
    if isinstance(value, str):
        expanded = _expand_env(value)
        if expanded == "":
            return None
        if expanded.startswith("${") and expanded.endswith("}"):
            return None
        return expanded
    return value


def _expand_env(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        default = match.group(2)
        env_value = os.getenv(name)
        if env_value:
            return env_value
        return default or ""

    return os.path.expandvars(ENV_PATTERN.sub(replace, value))


def load_settings(path: str | Path | None = None) -> Settings:
    explicit_path = path is not None or "STH_CONFIG" in os.environ
    config_path = Path(path or os.getenv("STH_CONFIG", DEFAULT_CONFIG_PATH))
    data: dict[str, Any] = {}
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    elif explicit_path:
        raise FileNotFoundError(f"Config file does not exist: {config_path}")
    data = _clean_empty_env_values(data)
    return Settings(**data)
