from __future__ import annotations

from server_tg_home.core.config import Settings
from server_tg_home.media.camera_health import evaluate_single_camera_health
from server_tg_home.webapp.schemas import (
    CameraHealthItem,
    CameraItem,
    CameraList,
)


def visible_camera_ids(settings: Settings) -> list[str]:
    return [
        camera_id
        for camera_id, camera in settings.cameras.items()
        if camera.web_enabled
    ]


def get_cameras(settings: Settings) -> CameraList:
    items: list[CameraItem] = []
    for camera_id in visible_camera_ids(settings):
        camera = settings.cameras[camera_id]
        status = evaluate_single_camera_health(settings, camera_id)
        items.append(
            CameraItem(
                id=camera_id,
                title=camera.title or camera_id,
                live_available=bool(camera.go2rtc_stream),
                health=CameraHealthItem(
                    state=status.state,
                    available=status.ok,
                    reason=status.reason,
                    last_segment_at=status.last_segment_at,
                    last_segment_age_sec=status.last_segment_age_sec,
                ),
            )
        )
    return CameraList(items=items)
