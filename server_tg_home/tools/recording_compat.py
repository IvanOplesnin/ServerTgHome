from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
import fcntl
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
from typing import Iterator, Sequence

from sqlalchemy import create_engine, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from server_tg_home.core.config import Settings, load_settings
from server_tg_home.database.models import Video


MAX_WIDTH = 1920
MAX_HEIGHT = 1080
MAX_H264_LEVEL = 41
MAX_FPS = Fraction(30, 1)
TARGET_AUDIO_RATE = 48_000
DURATION_TOLERANCE_SEC = 1.0
DURATION_TOLERANCE_RATIO = 0.02
CAMERA_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")
TEMP_FILE_MARKER = "recording-compat"
LOCK_FILENAME = ".recording-compat.lock"


class MigrationConfigurationError(ValueError):
    """The migration cannot run safely with the supplied configuration."""


class RecordingMigrationError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class MediaInfo:
    format_names: frozenset[str]
    duration_sec: float
    video_stream_count: int
    video_codec: str | None
    video_profile: str | None
    video_level: int | None
    width: int | None
    height: int | None
    pixel_format: str | None
    fps: Fraction | None
    audio_stream_count: int
    audio_codec: str | None
    audio_sample_rate: int | None
    audio_channels: int | None
    moov_before_mdat: bool


@dataclass(frozen=True)
class VideoSelection:
    video_id: int
    camera_id: str
    path_value: str
    size_bytes: int


@dataclass(frozen=True)
class MigrationOptions:
    camera_id: str
    backup_dir: Path
    apply: bool


@dataclass
class MigrationSummary:
    selected: int = 0
    pending: int = 0
    compatible: int = 0
    migrated: int = 0
    metadata_updated: int = 0
    failed: int = 0
    failure_reasons: Counter[str] = field(default_factory=Counter)

    def add_failure(self, reason: str) -> None:
        self.failed += 1
        self.failure_reasons[reason] += 1


def run_migration(
    settings: Settings,
    options: MigrationOptions,
) -> MigrationSummary:
    """Inspect or migrate active recordings without exposing their paths."""

    _validate_camera_id(options.camera_id)
    storage_root = _resolved_storage_root(settings.storage.path)
    backup_root = _validated_backup_root(
        options.backup_dir,
        storage_root=storage_root,
        create=options.apply,
    )
    engine = create_engine(
        settings.app.database_url,
        future=True,
        pool_pre_ping=True,
    )
    summary = MigrationSummary()
    lock = (
        _exclusive_lock(storage_root.parent / LOCK_FILENAME)
        if options.apply
        else nullcontext()
    )
    try:
        selections = _select_active_videos(
            engine,
            camera_id=options.camera_id,
        )
        summary.selected = len(selections)
        with lock:
            for selection in selections:
                try:
                    _process_selection(
                        engine,
                        selection=selection,
                        storage_root=storage_root,
                        backup_root=backup_root,
                        apply=options.apply,
                        summary=summary,
                    )
                except RecordingMigrationError as exc:
                    summary.add_failure(exc.reason)
                except Exception:
                    # Deliberately do not propagate exception text: database
                    # exceptions and media tools may embed private paths or URLs.
                    summary.add_failure("unexpected_failure")
    finally:
        engine.dispose()
    return summary


def _process_selection(
    engine: Engine,
    *,
    selection: VideoSelection,
    storage_root: Path,
    backup_root: Path,
    apply: bool,
    summary: MigrationSummary,
) -> None:
    source, relative_path = _resolve_video_path(
        selection.path_value,
        storage_root=storage_root,
    )
    original_stat = source.stat()
    original_info = _probe_media(source)

    if _is_browser_compatible(original_info):
        summary.compatible += 1
        if original_stat.st_size != selection.size_bytes:
            summary.pending += 1
            if apply:
                _update_database_size(
                    engine,
                    selection=selection,
                    size_bytes=original_stat.st_size,
                )
                summary.metadata_updated += 1
        return

    summary.pending += 1
    if not apply:
        return

    backup_path = backup_root / relative_path
    _ensure_hardlink_backup(
        source,
        backup_path,
        expected_stat=original_stat,
    )
    _assert_source_unchanged(source, original_stat)

    temporary_path = _temporary_output_path(
        source,
        video_id=selection.video_id,
    )
    _remove_stale_temporary(temporary_path)
    try:
        _run_transcode(
            source,
            temporary_path,
            source_info=original_info,
        )
        converted_info = _probe_media(temporary_path)
        _validate_converted_media(
            original=original_info,
            converted=converted_info,
        )
        _assert_source_unchanged(source, original_stat)
        _preserve_file_metadata(temporary_path, original_stat)
        _fsync_file(temporary_path)
        os.replace(temporary_path, source)
        _fsync_directory(source.parent)
    finally:
        _unlink_regular_temporary(temporary_path)

    converted_stat = source.stat()
    _update_database_size(
        engine,
        selection=selection,
        size_bytes=converted_stat.st_size,
    )
    summary.migrated += 1


def _select_active_videos(
    engine: Engine,
    *,
    camera_id: str,
) -> list[VideoSelection]:
    try:
        with Session(engine) as session:
            rows = session.execute(
                select(Video.id, Video.path, Video.size_bytes)
                .where(
                    Video.camera_id == camera_id,
                    Video.deleted_at.is_(None),
                )
                .order_by(Video.id.asc())
            ).all()
    except Exception as exc:
        raise MigrationConfigurationError(
            "Could not read active recordings from the database"
        ) from exc
    return [
        VideoSelection(
            video_id=int(row.id),
            camera_id=camera_id,
            path_value=str(row.path),
            size_bytes=int(row.size_bytes),
        )
        for row in rows
    ]


def _update_database_size(
    engine: Engine,
    *,
    selection: VideoSelection,
    size_bytes: int,
) -> None:
    try:
        with Session(engine) as session:
            result = session.execute(
                update(Video)
                .where(
                    Video.id == selection.video_id,
                    Video.camera_id == selection.camera_id,
                    Video.path == selection.path_value,
                    Video.deleted_at.is_(None),
                )
                .values(size_bytes=size_bytes)
            )
            if result.rowcount != 1:
                session.rollback()
                raise RecordingMigrationError("database_row_changed")
            session.commit()
    except RecordingMigrationError:
        raise
    except Exception as exc:
        raise RecordingMigrationError("database_update_failed") from exc


def _resolved_storage_root(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
        file_stat = resolved.stat()
    except (OSError, RuntimeError) as exc:
        raise MigrationConfigurationError("Storage root is unavailable") from exc
    if not stat.S_ISDIR(file_stat.st_mode):
        raise MigrationConfigurationError("Storage root is not a directory")
    return resolved


def _validated_backup_root(
    path: Path,
    *,
    storage_root: Path,
    create: bool,
) -> Path:
    if not path.is_absolute():
        raise MigrationConfigurationError("Backup directory must be absolute")
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise MigrationConfigurationError("Backup directory is invalid") from exc

    if (
        resolved == storage_root
        or resolved.is_relative_to(storage_root)
        or storage_root.is_relative_to(resolved)
    ):
        raise MigrationConfigurationError(
            "Backup directory must be separate from the recording storage root"
        )

    ancestor = _nearest_existing_ancestor(resolved)
    try:
        if ancestor.stat().st_dev != storage_root.stat().st_dev:
            raise MigrationConfigurationError(
                "Backup directory must be on the recording filesystem"
            )
        if create:
            resolved.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not resolved.is_dir():
                raise MigrationConfigurationError(
                    "Backup directory is not a directory"
                )
            if resolved.stat().st_dev != storage_root.stat().st_dev:
                raise MigrationConfigurationError(
                    "Backup directory must be on the recording filesystem"
                )
    except MigrationConfigurationError:
        raise
    except OSError as exc:
        raise MigrationConfigurationError(
            "Backup directory is unavailable"
        ) from exc
    return resolved


def _nearest_existing_ancestor(path: Path) -> Path:
    candidate = path
    while not candidate.exists():
        parent = candidate.parent
        if parent == candidate:
            raise MigrationConfigurationError(
                "Backup directory has no accessible parent"
            )
        candidate = parent
    if not candidate.is_dir():
        raise MigrationConfigurationError(
            "Backup directory parent is not a directory"
        )
    return candidate


def _resolve_video_path(
    stored_value: str,
    *,
    storage_root: Path,
) -> tuple[Path, Path]:
    candidate = Path(stored_value)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        resolved = candidate.resolve(strict=True)
        file_stat = resolved.stat()
    except (OSError, RuntimeError) as exc:
        raise RecordingMigrationError("recording_unavailable") from exc
    if not resolved.is_relative_to(storage_root):
        raise RecordingMigrationError("unsafe_recording_path")
    if not stat.S_ISREG(file_stat.st_mode) or resolved.suffix.lower() != ".mp4":
        raise RecordingMigrationError("unsupported_recording_file")
    return resolved, resolved.relative_to(storage_root)


def _ensure_hardlink_backup(
    source: Path,
    backup: Path,
    *,
    expected_stat: os.stat_result,
) -> None:
    try:
        backup.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if backup.exists():
            backup_stat = backup.stat()
            if (
                not stat.S_ISREG(backup_stat.st_mode)
                or not os.path.samefile(source, backup)
            ):
                raise RecordingMigrationError("backup_collision")
            return
        os.link(source, backup)
        if not os.path.samefile(source, backup):
            raise RecordingMigrationError("backup_verification_failed")
        backup_stat = backup.stat()
        if (
            backup_stat.st_dev != expected_stat.st_dev
            or backup_stat.st_ino != expected_stat.st_ino
        ):
            raise RecordingMigrationError("backup_verification_failed")
        _fsync_directory(backup.parent)
    except RecordingMigrationError:
        raise
    except FileExistsError:
        try:
            if not os.path.samefile(source, backup):
                raise RecordingMigrationError("backup_collision")
        except OSError as exc:
            raise RecordingMigrationError("backup_failed") from exc
    except OSError as exc:
        raise RecordingMigrationError("backup_failed") from exc


def _temporary_output_path(source: Path, *, video_id: int) -> Path:
    return source.with_name(
        f".{source.stem}.{TEMP_FILE_MARKER}-{video_id}.tmp.mp4"
    )


def _remove_stale_temporary(path: Path) -> None:
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise RecordingMigrationError("temporary_file_failed") from exc
    if not stat.S_ISREG(file_stat.st_mode):
        raise RecordingMigrationError("unsafe_temporary_file")
    try:
        path.unlink()
    except OSError as exc:
        raise RecordingMigrationError("temporary_file_failed") from exc


def _unlink_regular_temporary(path: Path) -> None:
    try:
        file_stat = path.lstat()
    except FileNotFoundError:
        return
    except OSError:
        return
    if stat.S_ISREG(file_stat.st_mode):
        try:
            path.unlink()
        except OSError:
            pass


def _run_transcode(
    source: Path,
    output: Path,
    *,
    source_info: MediaInfo,
) -> None:
    command = _build_ffmpeg_command(source, output)
    timeout = max(300.0, source_info.duration_sec * 12.0 + 120.0)
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RecordingMigrationError("transcode_failed") from exc
    if completed.returncode != 0:
        raise RecordingMigrationError("transcode_failed")


def _build_ffmpeg_command(source: Path, output: Path) -> list[str]:
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-n",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        "scale=w='min(1920,iw)':h='min(1080,ih)':"
        "force_original_aspect_ratio=decrease:force_divisible_by=2:"
        "out_range=tv,format=yuv420p",
        "-fpsmax",
        "30",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "23",
        "-profile:v",
        "high",
        "-level:v",
        "4.1",
        "-pix_fmt",
        "yuv420p",
        "-color_range",
        "tv",
        "-tag:v",
        "avc1",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        str(TARGET_AUDIO_RATE),
        "-ac",
        "1",
        "-movflags",
        "+faststart",
        str(output),
    ]


def _probe_media(path: Path) -> MediaInfo:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration,format_name:"
        "stream=codec_type,codec_name,profile,level,width,height,pix_fmt,"
        "r_frame_rate,avg_frame_rate,sample_rate,channels",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RecordingMigrationError("media_probe_failed") from exc
    if completed.returncode != 0:
        raise RecordingMigrationError("media_probe_failed")
    try:
        payload = json.loads(completed.stdout)
        format_payload = payload.get("format") or {}
        streams = payload.get("streams") or []
        video_streams = [
            item for item in streams if item.get("codec_type") == "video"
        ]
        audio_streams = [
            item for item in streams if item.get("codec_type") == "audio"
        ]
        video = video_streams[0] if video_streams else {}
        audio = audio_streams[0] if audio_streams else {}
        duration = float(format_payload.get("duration"))
        if not math.isfinite(duration):
            raise ValueError
        return MediaInfo(
            format_names=frozenset(
                str(format_payload.get("format_name") or "").split(",")
            ),
            duration_sec=duration,
            video_stream_count=len(video_streams),
            video_codec=_optional_string(video.get("codec_name")),
            video_profile=_optional_string(video.get("profile")),
            video_level=_optional_int(video.get("level")),
            width=_optional_int(video.get("width")),
            height=_optional_int(video.get("height")),
            pixel_format=_optional_string(video.get("pix_fmt")),
            fps=_stream_fps(video),
            audio_stream_count=len(audio_streams),
            audio_codec=_optional_string(audio.get("codec_name")),
            audio_sample_rate=_optional_int(audio.get("sample_rate")),
            audio_channels=_optional_int(audio.get("channels")),
            moov_before_mdat=_moov_before_mdat(path),
        )
    except (TypeError, ValueError, AttributeError, json.JSONDecodeError) as exc:
        raise RecordingMigrationError("media_probe_failed") from exc


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _stream_fps(stream: dict[str, object]) -> Fraction | None:
    for key in ("avg_frame_rate", "r_frame_rate"):
        raw = stream.get(key)
        if raw in (None, "", "0/0"):
            continue
        try:
            value = Fraction(str(raw))
        except (ValueError, ZeroDivisionError):
            continue
        if value > 0:
            return value
    return None


def _is_browser_compatible(info: MediaInfo) -> bool:
    if "mp4" not in info.format_names:
        return False
    if not math.isfinite(info.duration_sec) or info.duration_sec <= 0:
        return False
    if info.video_stream_count != 1:
        return False
    if (info.video_codec or "").casefold() != "h264":
        return False
    if (info.video_profile or "").casefold() != "high":
        return False
    if info.video_level is None or not 1 <= info.video_level <= MAX_H264_LEVEL:
        return False
    if (
        info.width is None
        or info.height is None
        or info.width <= 0
        or info.height <= 0
        or info.width > MAX_WIDTH
        or info.height > MAX_HEIGHT
        or info.width % 2
        or info.height % 2
    ):
        return False
    if info.pixel_format != "yuv420p":
        return False
    if info.fps is None or not 0 < info.fps <= MAX_FPS:
        return False
    if info.audio_stream_count > 1:
        return False
    if info.audio_stream_count == 1 and (
        (info.audio_codec or "").casefold() != "aac"
        or info.audio_sample_rate != TARGET_AUDIO_RATE
        or info.audio_channels is None
        or not 1 <= info.audio_channels <= 2
    ):
        return False
    return info.moov_before_mdat


def _validate_converted_media(
    *,
    original: MediaInfo,
    converted: MediaInfo,
) -> None:
    if not _is_browser_compatible(converted):
        raise RecordingMigrationError("converted_media_incompatible")
    if original.audio_stream_count > 0 and converted.audio_stream_count != 1:
        raise RecordingMigrationError("converted_media_incompatible")
    tolerance = max(
        DURATION_TOLERANCE_SEC,
        original.duration_sec * DURATION_TOLERANCE_RATIO,
    )
    if abs(converted.duration_sec - original.duration_sec) > tolerance:
        raise RecordingMigrationError("converted_duration_mismatch")


def _moov_before_mdat(path: Path) -> bool:
    try:
        file_size = path.stat().st_size
        moov_offset: int | None = None
        mdat_offset: int | None = None
        offset = 0
        with path.open("rb") as handle:
            for _ in range(100_000):
                if offset + 8 > file_size:
                    break
                handle.seek(offset)
                header = handle.read(8)
                if len(header) != 8:
                    return False
                size_32, atom_type = struct.unpack(">I4s", header)
                header_size = 8
                if size_32 == 1:
                    extended = handle.read(8)
                    if len(extended) != 8:
                        return False
                    atom_size = struct.unpack(">Q", extended)[0]
                    header_size = 16
                elif size_32 == 0:
                    atom_size = file_size - offset
                else:
                    atom_size = size_32
                if (
                    atom_size < header_size
                    or offset + atom_size > file_size
                ):
                    return False
                if atom_type == b"moov" and moov_offset is None:
                    moov_offset = offset
                elif atom_type == b"mdat" and mdat_offset is None:
                    mdat_offset = offset
                if moov_offset is not None and mdat_offset is not None:
                    return moov_offset < mdat_offset
                if atom_size == 0:
                    break
                offset += atom_size
    except OSError:
        return False
    return False


def _assert_source_unchanged(
    source: Path,
    expected: os.stat_result,
) -> None:
    try:
        current = source.stat()
    except OSError as exc:
        raise RecordingMigrationError("source_changed") from exc
    if (
        current.st_dev != expected.st_dev
        or current.st_ino != expected.st_ino
        or current.st_size != expected.st_size
        or current.st_mtime_ns != expected.st_mtime_ns
    ):
        raise RecordingMigrationError("source_changed")


def _preserve_file_metadata(
    path: Path,
    original: os.stat_result,
) -> None:
    try:
        current = path.stat()
        if (current.st_uid, current.st_gid) != (
            original.st_uid,
            original.st_gid,
        ):
            os.chown(path, original.st_uid, original.st_gid)
        os.chmod(path, stat.S_IMODE(original.st_mode))
        os.utime(
            path,
            ns=(original.st_atime_ns, original.st_mtime_ns),
            follow_symlinks=False,
        )
    except OSError as exc:
        raise RecordingMigrationError("metadata_preservation_failed") from exc


def _fsync_file(path: Path) -> None:
    try:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
    except OSError as exc:
        raise RecordingMigrationError("file_sync_failed") from exc


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise RecordingMigrationError("directory_sync_failed") from exc


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise MigrationConfigurationError(
            "Could not create the migration lock"
        ) from exc
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise MigrationConfigurationError(
                "Another recording migration is already running"
            ) from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _validate_camera_id(value: str) -> None:
    if CAMERA_ID_PATTERN.fullmatch(value) is None:
        raise MigrationConfigurationError("Camera identifier is invalid")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m server_tg_home.tools.recording_compat",
        description=(
            "Safely migrate active camera recordings to a browser-compatible MP4."
        ),
    )
    parser.add_argument("--config", help="Path to the runtime YAML config")
    parser.add_argument("--camera", required=True)
    parser.add_argument("--backup-dir", required=True, type=Path)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        settings = load_settings(args.config)
        options = MigrationOptions(
            camera_id=args.camera,
            backup_dir=args.backup_dir,
            apply=bool(args.apply),
        )
        summary = run_migration(settings, options)
    except (MigrationConfigurationError, FileNotFoundError):
        print(
            "Recording migration could not start because a safety check failed.",
            file=sys.stderr,
        )
        return 2
    except Exception:
        # Never render arbitrary exception strings: they may contain a database
        # URL, a local path, or media-tool command arguments.
        print(
            "Recording migration stopped after an unexpected failure.",
            file=sys.stderr,
        )
        return 2

    mode = "apply" if options.apply else "dry-run"
    print(f"mode={mode} camera={options.camera_id}")
    print(
        "selected={selected} pending={pending} compatible={compatible} "
        "migrated={migrated} metadata_updated={metadata_updated} "
        "failed={failed}".format(
            selected=summary.selected,
            pending=summary.pending,
            compatible=summary.compatible,
            migrated=summary.migrated,
            metadata_updated=summary.metadata_updated,
            failed=summary.failed,
        )
    )
    if summary.failure_reasons:
        reasons = ",".join(
            f"{reason}:{count}"
            for reason, count in sorted(summary.failure_reasons.items())
        )
        print(f"failure_reasons={reasons}", file=sys.stderr)
    return 1 if summary.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
