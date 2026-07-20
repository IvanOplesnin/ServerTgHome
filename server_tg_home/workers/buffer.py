from __future__ import annotations

import logging
import subprocess
import time

from server_tg_home.core.config import Settings
from server_tg_home.media.recorder import build_buffer_command, cleanup_old_buffer_segments
from server_tg_home.media.storage import ensure_storage

logger = logging.getLogger(__name__)


class BufferWorker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.processes: dict[str, subprocess.Popen] = {}
        self.last_restart_at: dict[str, float] = {}
        self.started_at: dict[str, float] = {}

    def run_forever(self) -> None:
        if not self.settings.buffer.enabled:
            logger.info("Buffer worker is disabled by config")
            return
        ensure_storage(self.settings)
        logger.info("Buffer worker started")
        try:
            while True:
                self._tick()
                time.sleep(2)
        finally:
            self._stop_all()

    def _tick(self) -> None:
        for camera_id, camera in self.settings.cameras.items():
            if not camera.buffer_enabled:
                continue
            cleanup_old_buffer_segments(self.settings, camera_id)
            process = self.processes.get(camera_id)
            if process is not None and process.poll() is None:
                if self._buffer_is_stale(camera_id):
                    logger.warning(
                        "Buffer process for %s is alive but has no fresh segments; restarting",
                        camera_id,
                    )
                    self._restart_camera(camera_id)
                continue
            if process is not None:
                logger.warning("Buffer process for %s exited with code %s", camera_id, process.returncode)
            now = time.monotonic()
            last_restart = self.last_restart_at.get(camera_id, 0)
            if now - last_restart < self.settings.buffer.restart_delay_sec:
                continue
            self._start_camera(camera_id)

    def _start_camera(self, camera_id: str) -> None:
        camera = self.settings.cameras[camera_id]
        command = build_buffer_command(self.settings, camera_id, camera)
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.processes[camera_id] = process
        self.last_restart_at[camera_id] = time.monotonic()
        self.started_at[camera_id] = time.monotonic()
        logger.info("Started buffer process for camera %s", camera_id)

    def _restart_camera(self, camera_id: str) -> None:
        now = time.monotonic()
        last_restart = self.last_restart_at.get(camera_id, 0)
        if now - last_restart < self.settings.buffer.restart_delay_sec:
            return
        process = self.processes.get(camera_id)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        self._start_camera(camera_id)

    def _buffer_is_stale(self, camera_id: str) -> bool:
        started_at = self.started_at.get(camera_id, self.last_restart_at.get(camera_id, 0))
        stale_after_sec = self._stale_after_sec()
        if time.monotonic() - started_at < stale_after_sec:
            return False

        latest_mtime = self._latest_segment_mtime(camera_id)
        if latest_mtime is None:
            return True
        return time.time() - latest_mtime > stale_after_sec

    def _latest_segment_mtime(self, camera_id: str) -> float | None:
        latest_mtime: float | None = None
        buffer_path = self.settings.buffer.path / camera_id
        for segment in buffer_path.glob("*.mp4"):
            try:
                stat = segment.stat()
            except FileNotFoundError:
                continue
            if stat.st_size < 1024:
                continue
            mtime = stat.st_mtime
            if latest_mtime is None or mtime > latest_mtime:
                latest_mtime = mtime
        return latest_mtime

    def _stale_after_sec(self) -> int:
        configured = self.settings.camera_health.stale_after_sec
        if configured is not None and configured > 0:
            return configured
        return max(self.settings.buffer.keep_seconds, self.settings.buffer.segment_seconds * 3, 30)

    def _stop_all(self) -> None:
        for camera_id, process in self.processes.items():
            if process.poll() is not None:
                continue
            logger.info("Stopping buffer process for camera %s", camera_id)
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
