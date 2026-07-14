from __future__ import annotations

from typing import Any

import httpx

from server_tg_home.core.config import HomeAssistantConfig


class HomeAssistantError(RuntimeError):
    pass


class HomeAssistantClient:
    def __init__(self, config: HomeAssistantConfig) -> None:
        self.config = config

    def call_service(self, domain: str, service: str, data: dict[str, Any]) -> dict[str, Any]:
        if not self.config.base_url or not self.config.token:
            raise HomeAssistantError("Home Assistant base_url and token are required")

        url = self.config.base_url.rstrip("/") + f"/api/services/{domain}/{service}"
        headers = {
            "Authorization": f"Bearer {self.config.token}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.config.request_timeout_sec) as client:
            response = client.post(url, headers=headers, json=data)
        if response.status_code >= 400:
            raise HomeAssistantError(f"Home Assistant error {response.status_code}: {response.text[:500]}")
        try:
            return response.json()
        except ValueError:
            return {"ok": True}
