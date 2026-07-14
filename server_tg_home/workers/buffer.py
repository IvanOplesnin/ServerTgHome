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
        logger.info("Started buffer process for camera %s", camera_id)

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
