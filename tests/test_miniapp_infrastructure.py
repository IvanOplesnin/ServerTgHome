from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import yaml

from server_tg_home import cli
from server_tg_home.core.config import load_settings


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class MiniAppInfrastructureTests(unittest.TestCase):
    def test_three_web_cameras_match_go2rtc_streams(self) -> None:
        settings = load_settings(
            REPOSITORY_ROOT / "config" / "config.example.yaml"
        )
        go2rtc = yaml.safe_load(
            (REPOSITORY_ROOT / "config" / "go2rtc.example.yaml").read_text()
        )
        camera_ids = {"entrance", "living", "bed"}

        self.assertTrue(camera_ids.issubset(settings.cameras))
        self.assertTrue(
            all(settings.cameras[camera_id].web_enabled for camera_id in camera_ids)
        )
        self.assertEqual(
            {
                settings.cameras[camera_id].go2rtc_stream
                for camera_id in camera_ids
            },
            camera_ids,
        )
        self.assertTrue(camera_ids.issubset(go2rtc["streams"]))
        self.assertEqual(go2rtc["webrtc"]["listen"], ":8555")

    def test_compose_keeps_management_ports_private(self) -> None:
        compose = yaml.safe_load(
            (REPOSITORY_ROOT / "docker-compose.yml").read_text()
        )
        services = compose["services"]

        self.assertEqual(services["api"]["ports"], ["127.0.0.1:18080:8080"])
        self.assertIn("--no-access-log", services["api"]["command"])
        self.assertIn("127.0.0.1:1984:1984", services["go2rtc"]["ports"])
        self.assertIn("8555:8555/tcp", services["go2rtc"]["ports"])
        self.assertIn("8555:8555/udp", services["go2rtc"]["ports"])
        self.assertFalse(
            any(
                "8554" in str(port)
                for service in services.values()
                for port in service.get("ports", [])
            )
        )
        self.assertEqual(
            set(services["miniapp-gateway"]["ports"]),
            {"80:80", "443:443"},
        )
        self.assertEqual(services["miniapp-gateway"]["profiles"], ["miniapp"])
        self.assertNotIn("ports", services["postgres"])
        self.assertNotIn("ports", services["redis"])

    def test_caddy_has_strict_api_and_media_allowlists(self) -> None:
        caddyfile = (
            REPOSITORY_ROOT / "docker" / "miniapp.Caddyfile"
        ).read_text()

        self.assertIn("@webapp_api path /api/webapp /api/webapp/*", caddyfile)
        self.assertIn("@blocked_api path /api /api/*", caddyfile)
        self.assertIn(
            "@player_scripts path /media/video-stream.js /media/video-rtc.js",
            caddyfile,
        )
        for endpoint in (
            "ws",
            "stream[.]m3u8",
            "playlist[.]m3u8",
            "segment[.]ts",
            "init[.]mp4",
            "segment[.]m4s",
        ):
            self.assertIn(endpoint, caddyfile)
        self.assertIn("forward_auth api:8080", caddyfile)
        self.assertIn("method GET HEAD", caddyfile)
        self.assertIn("stream_timeout 10m", caddyfile)
        self.assertIn("@blocked_media path /media /media/*", caddyfile)
        self.assertNotIn("handle_path /media/api", caddyfile)
        self.assertNotIn("\n\tlog {", caddyfile)

    def test_runtime_secrets_are_excluded_from_build_context(self) -> None:
        patterns = set(
            (REPOSITORY_ROOT / ".dockerignore").read_text().splitlines()
        )

        self.assertIn(".env*", patterns)
        self.assertIn("config/config.yaml", patterns)
        self.assertIn("config/config.yaml.*", patterns)
        self.assertIn("config/go2rtc.yaml", patterns)
        self.assertIn("config/go2rtc.yaml.*", patterns)
        self.assertIn("compose.yaml", patterns)

    @patch("server_tg_home.cli.uvicorn.run")
    def test_production_cli_can_disable_url_access_logs(self, run) -> None:
        argv = [
            "server-tg-home",
            "api",
            "--host",
            "0.0.0.0",
            "--port",
            "8080",
            "--no-access-log",
        ]
        with patch.object(sys, "argv", argv):
            cli.main()

        self.assertFalse(run.call_args.kwargs["access_log"])


if __name__ == "__main__":
    unittest.main()
