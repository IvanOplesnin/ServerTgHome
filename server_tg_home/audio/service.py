from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx

from server_tg_home.core.config import CameraConfig, Settings

logger = logging.getLogger(__name__)


class AudioPlaybackError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedAudio:
    path: Path
    size_bytes: int


class Go2RtcAudioClient:
    def __init__(
        self,
        base_url: str,
        timeout_sec: int,
        *,
        restart_before_playback: bool,
        restart_wait_sec: int,
        restart_poll_sec: float,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_sec = timeout_sec
        self.restart_before_playback = restart_before_playback
        self.restart_wait_sec = restart_wait_sec
        self.restart_poll_sec = restart_poll_sec

    def play_file(
        self,
        *,
        stream_name: str,
        path: Path,
        codec: str,
        duration_sec: int,
        grace_sec: int,
    ) -> None:
        src = f"ffmpeg:{path}#audio={codec}#input=file"
        timeout = httpx.Timeout(float(self.timeout_sec))
        if self.restart_before_playback:
            self.restart_and_wait_for_talkback(stream_name=stream_name, codec=codec)
        with httpx.Client(timeout=timeout) as client:
            response = client.post(
                f"{self.base_url}/api/streams",
                params={"dst": stream_name, "src": src},
            )
            _raise_for_status(response, action="start", stream_name=stream_name)
            try:
                time.sleep(max(0, duration_sec) + max(0, grace_sec))
            finally:
                stop_response = client.post(
                    f"{self.base_url}/api/streams",
                    params={"dst": stream_name, "src": ""},
                )
                _raise_for_status(stop_response, action="stop", stream_name=stream_name)

    def restart_and_wait_for_talkback(self, *, stream_name: str, codec: str) -> None:
        timeout = httpx.Timeout(float(self.timeout_sec))
        with httpx.Client(timeout=timeout) as client:
            response = client.post(f"{self.base_url}/api/restart")
            _raise_for_status(response, action="restart", stream_name=stream_name)

        wait_sec = max(0.0, float(self.restart_wait_sec))
        poll_sec = max(0.1, float(self.restart_poll_sec))
        deadline = time.monotonic() + wait_sec
        last_state = "go2rtc restart requested"

        while True:
            try:
                stream = self.get_stream(stream_name)
                if _stream_has_talkback_audio(stream, codec):
                    return
                last_state = _describe_talkback_state(stream)
            except Exception as exc:
                last_state = str(exc)

            now = time.monotonic()
            if now >= deadline:
                break
            time.sleep(min(poll_sec, deadline - now))

        raise AudioPlaybackError(
            f"go2rtc talkback is not ready for {stream_name} after restart: {last_state}"
        )

    def get_stream(self, stream_name: str) -> dict:
        timeout = httpx.Timeout(float(self.timeout_sec))
        with httpx.Client(timeout=timeout) as client:
            response = client.get(
                f"{self.base_url}/api/streams",
                params={"src": stream_name},
            )
            _raise_for_status(response, action="inspect", stream_name=stream_name)
            data = response.json()
        if isinstance(data, dict) and isinstance(data.get(stream_name), dict):
            return data[stream_name]
        if isinstance(data, dict):
            return data
        raise AudioPlaybackError(f"go2rtc inspect failed for {stream_name}: unexpected response")


def make_voice_source_path(
    settings: Settings,
    *,
    camera_id: str,
    chat_id: int,
    message_thread_id: int | None,
    message_id: int,
    file_unique_id: str,
) -> Path:
    now = datetime.now(UTC)
    directory = settings.audio.path / camera_id / now.strftime("%Y-%m-%d") / "source"
    directory.mkdir(parents=True, exist_ok=True)
    thread = message_thread_id if message_thread_id is not None else 0
    unique = _safe_filename_part(file_unique_id)[:32]
    filename = f"{now.strftime('%H%M%S')}_{chat_id}_{thread}_{message_id}_{unique}.ogg"
    return directory / filename


def make_prepared_audio_path(settings: Settings, *, camera_id: str, job_id: str) -> Path:
    now = datetime.now(UTC)
    directory = settings.audio.path / camera_id / now.strftime("%Y-%m-%d") / "prepared"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{now.strftime('%H%M%S')}_{job_id}.wav"


def prepare_camera_audio(settings: Settings, source_path: Path, output_path: Path) -> PreparedAudio:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-y",
        "-i",
        str(source_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "8000",
        "-c:a",
        "pcm_alaw",
        str(output_path),
    ]
    _run(command, timeout_sec=max(settings.audio.playback_timeout_sec, 30))
    return PreparedAudio(path=output_path, size_bytes=output_path.stat().st_size)


def play_camera_audio(
    settings: Settings,
    camera_id: str,
    camera: CameraConfig,
    prepared_path: Path,
    duration_sec: int,
) -> None:
    stream_name = camera.go2rtc_stream or camera_id
    codec = camera.speaker_audio_codec or settings.audio.default_codec
    client = Go2RtcAudioClient(
        settings.audio.go2rtc_base_url,
        timeout_sec=settings.audio.playback_timeout_sec,
        restart_before_playback=settings.audio.go2rtc_restart_before_playback,
        restart_wait_sec=settings.audio.go2rtc_restart_wait_sec,
        restart_poll_sec=settings.audio.go2rtc_restart_poll_sec,
    )
    try:
        client.play_file(
            stream_name=stream_name,
            path=prepared_path,
            codec=codec,
            duration_sec=duration_sec,
            grace_sec=settings.audio.playback_grace_sec,
        )
    except httpx.HTTPError as exc:
        raise AudioPlaybackError(f"go2rtc playback failed for {stream_name}: {exc}") from exc


def _raise_for_status(response: httpx.Response, *, action: str, stream_name: str) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = response.text.strip()
        detail = f"go2rtc {action} failed for {stream_name}: {exc}"
        if body:
            detail = f"{detail}; response: {body[:500]}"
        raise AudioPlaybackError(detail) from exc


def _stream_has_talkback_audio(stream: dict, codec: str) -> bool:
    codec_label = _codec_label(codec)
    for producer in stream.get("producers") or []:
        if producer.get("error"):
            continue
        for media in producer.get("medias") or []:
            text = str(media).upper()
            if "AUDIO" not in text or "SENDONLY" not in text:
                continue
            if codec_label is None or codec_label in text:
                return True
    return False


def _describe_talkback_state(stream: dict) -> str:
    producer_descriptions: list[str] = []
    for producer in stream.get("producers") or []:
        medias = ", ".join(str(media) for media in producer.get("medias") or [])
        error = producer.get("error")
        remote = producer.get("remote_addr") or "unknown remote"
        if error:
            producer_descriptions.append(f"{remote}: error={error}")
        elif medias:
            producer_descriptions.append(f"{remote}: {medias}")
        else:
            producer_descriptions.append(f"{remote}: no medias")
    if producer_descriptions:
        return "; ".join(producer_descriptions)
    return "no producers"


def _codec_label(codec: str) -> str | None:
    normalized = codec.strip().lower()
    if not normalized:
        return None
    aliases = {
        "alaw": "PCMA",
        "g711a": "PCMA",
        "pcm_alaw": "PCMA",
        "pcma": "PCMA",
        "mulaw": "PCMU",
        "g711u": "PCMU",
        "pcm_mulaw": "PCMU",
        "pcmu": "PCMU",
    }
    return aliases.get(normalized, normalized.upper())


def _run(command: list[str], timeout_sec: int) -> None:
    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_sec,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr[-2000:] if result.stderr else "unknown ffmpeg error"
        raise AudioPlaybackError(stderr)


def _safe_filename_part(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)
    return safe or quote(value, safe="") or "file"
