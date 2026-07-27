from __future__ import annotations

import unittest

from pydantic import ValidationError

from server_tg_home.core.config import CameraConfig, Settings, WebAppConfig


class WebAppConfigTests(unittest.TestCase):
    def test_defaults_are_disabled_and_fail_closed(self) -> None:
        settings = Settings()

        self.assertFalse(settings.webapp.enabled)
        self.assertEqual(settings.webapp.viewer_user_ids, [])
        self.assertTrue(settings.webapp.require_group_membership)
        self.assertEqual(settings.webapp.membership_recheck_sec, 300)
        self.assertEqual([tab.id for tab in settings.webapp.tabs], ["cameras", "climate"])

    def test_legacy_allowed_user_ids_alias_is_accepted(self) -> None:
        config = WebAppConfig(allowed_user_ids=[42])  # type: ignore[call-arg]

        self.assertEqual(config.viewer_user_ids, [42])

    def test_enabled_webapp_requires_https_public_url(self) -> None:
        with self.assertRaises(ValidationError):
            WebAppConfig(enabled=True)
        with self.assertRaises(ValidationError):
            WebAppConfig(enabled=True, public_url="http://home.example.com")

    def test_duplicate_viewers_and_tabs_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            WebAppConfig(viewer_user_ids=[42, 42])
        with self.assertRaises(ValidationError):
            WebAppConfig(
                tabs=[
                    {"id": "cameras", "title": "One", "kind": "cameras"},
                    {"id": "cameras", "title": "Two", "kind": "climate"},
                ]
            )

    def test_capability_ticket_lifetimes_are_bounded(self) -> None:
        with self.assertRaises(ValidationError):
            WebAppConfig(media_ticket_ttl_sec=3601)
        with self.assertRaises(ValidationError):
            WebAppConfig(video_ticket_ttl_sec=29)

    def test_camera_web_fields_are_safe_by_default(self) -> None:
        camera = CameraConfig(rtsp_url="rtsp://camera.local/stream")

        self.assertIsNone(camera.title)
        self.assertFalse(camera.web_enabled)
