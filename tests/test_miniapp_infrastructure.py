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
        expected_streams = {
            "entrance": "entrance_web",
            "living": "living",
            "bed": "bed",
        }
        self.assertEqual(
            {
                camera_id: settings.cameras[camera_id].go2rtc_stream
                for camera_id in camera_ids
            },
            expected_streams,
        )
        self.assertTrue(camera_ids.issubset(go2rtc["streams"]))
        self.assertIn("entrance_web", go2rtc["streams"])
        self.assertIn(
            "#video=h264#width=1920#height=-2#audio=aac",
            go2rtc["streams"]["entrance_web"][0],
        )
        self.assertEqual(go2rtc["webrtc"]["listen"], ":8555")

    def test_compose_uses_external_reverse_proxy_bindings(self) -> None:
        compose = yaml.safe_load(
            (REPOSITORY_ROOT / "docker-compose.yml").read_text()
        )
        services = compose["services"]
        proxy_bind = "${STH_REVERSE_PROXY_BIND_ADDRESS:-127.0.0.1}"

        self.assertEqual(
            services["api"]["ports"],
            [
                "127.0.0.1:18080:8080",
                f"{proxy_bind}:28080:8080",
            ],
        )
        self.assertIn("--no-access-log", services["api"]["command"])
        self.assertEqual(
            set(services["go2rtc"]["ports"]),
            {
                "127.0.0.1:1984:1984",
                f"{proxy_bind}:21984:1984",
                "8555:8555/tcp",
                "8555:8555/udp",
            },
        )
        self.assertFalse(
            any(
                "8554" in str(port)
                for service in services.values()
                for port in service.get("ports", [])
            )
        )
        self.assertEqual(
            services["miniapp-web"]["ports"],
            [f"{proxy_bind}:18082:8080"],
        )
        self.assertEqual(services["miniapp-web"]["profiles"], ["miniapp"])
        self.assertEqual(services["miniapp-web"]["cap_drop"], ["ALL"])
        self.assertTrue(services["miniapp-web"]["read_only"])
        self.assertNotIn("miniapp-gateway", services)
        self.assertFalse(
            {"caddy-data", "caddy-config"}.intersection(
                compose.get("volumes", {})
            )
        )
        self.assertFalse(
            any(
                str(port).split(":")[-2] in {"80", "443"}
                for service in services.values()
                for port in service.get("ports", [])
            )
        )
        self.assertNotIn("ports", services["postgres"])
        self.assertNotIn("ports", services["redis"])

    def test_miniapp_image_is_static_only_and_has_no_caddy(self) -> None:
        dockerfile = (
            REPOSITORY_ROOT / "docker" / "miniapp.Dockerfile"
        ).read_text()
        nginx_config = (
            REPOSITORY_ROOT / "docker" / "miniapp.nginx.conf"
        ).read_text()

        self.assertIn("nginxinc/nginx-unprivileged:", dockerfile)
        self.assertNotIn("caddy", dockerfile.lower())
        self.assertIn("listen 8080", nginx_config)
        self.assertIn("try_files $uri $uri/ /index.html", nginx_config)
        self.assertNotIn("proxy_pass", nginx_config)
        self.assertNotIn("location /api", nginx_config)
        self.assertNotIn("location /media", nginx_config)

    def test_external_caddy_template_has_strict_media_allowlist(self) -> None:
        deployment = (
            REPOSITORY_ROOT / "docs" / "telegram-mini-app-deployment.md"
        ).read_text()

        self.assertIn(
            "@webapp_api path /api/webapp /api/webapp/*",
            deployment,
        )
        self.assertIn(
            "@internal_webapp_api path /api/webapp/v1/media/authorize "
            "/api/webapp/v1/media/authorize/*",
            deployment,
        )
        self.assertIn("@blocked_api path /api /api/*", deployment)
        self.assertIn("path /media/video-stream.js /media/video-rtc.js", deployment)
        for endpoint in (
            "ws",
            "stream[.]m3u8",
            "playlist[.]m3u8",
            "segment[.]ts",
            "init[.]mp4",
            "segment[.]m4s",
        ):
            self.assertIn(endpoint, deployment)
        self.assertIn("forward_auth MINIPC_LAN_IP:28080", deployment)
        self.assertIn("reverse_proxy MINIPC_LAN_IP:21984", deployment)
        self.assertIn("method GET HEAD", deployment)
        self.assertIn("stream_timeout 10m", deployment)
        self.assertIn("@blocked_media path /media /media/*", deployment)
        self.assertNotIn("handle_path /media/api", deployment)

        deploy_script = (REPOSITORY_ROOT / "scripts" / "deploy.sh").read_text()
        self.assertIn("reverse_proxy_bind_is_safe", deploy_script)
        self.assertIn("0.0.0.0|::|\"[::]\"", deploy_script)

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
