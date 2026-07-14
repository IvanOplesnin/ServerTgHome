from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from server_tg_home.core.config import Settings

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi"}


def ensure_storage(settings: Settings) -> None:
    settings.storage.path.mkdir(parents=True, exist_ok=True)
    (settings.storage.path / "tmp").mkdir(parents=True, exist_ok=True)
    settings.buffer.path.mkdir(parents=True, exist_ok=True)


def make_clip_path(settings: Settings, camera_id: str, job_id: str) -> Path:
    now = datetime.now(UTC)
    directory = settings.storage.path / camera_id / now.strftime("%Y-%m-%d")
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{now.strftime('%H%M%S')}_{job_id}.mp4"
    return directory / filename


def make_snapshot_path(settings: Settings, camera_id: str, job_id: str) -> Path:
    now = datetime.now(UTC)
    directory = settings.storage.path / camera_id / now.strftime("%Y-%m-%d")
    directory.mkdir(parents=True, exist_ok=True)
    filename = f"{now.strftime('%H%M%S')}_{job_id}.jpg"
    return directory / filename


def make_tmp_path(settings: Settings, filename: str) -> Path:
    directory = settings.storage.path / "tmp"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / filename


def folder_size_bytes(path: Path) -> int:
    total = 0
    if not path.exists():
        return total
    for root, _, files in os.walk(path):
        for filename in files:
            file_path = Path(root) / filename
            try:
                total += file_path.stat().st_size
            except FileNotFoundError:
                continue
    return total


def iter_video_files(path: Path) -> list[Path]:
    if not path.exists():
        return []
    files: list[Path] = []
    for item in path.rglob("*"):
        if "tmp" in item.relative_to(path).parts:
            continue
        if item.is_file() and item.suffix.lower() in VIDEO_EXTENSIONS:
            files.append(item)
    return sorted(files, key=lambda item: item.stat().st_mtime)


def format_bytes(size: int) -> str:
    value = float(size)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def file_mtime_utc(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC)
