"""Authenticated control client for the headless PapaGUI index server."""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class IndexServerError(RuntimeError):
    """The server is unavailable or rejected a control request."""


class IndexServerClient:
    def __init__(self, server_url: str, token: str = "", timeout_seconds: float = 3):
        self.server_url = server_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def status(self) -> dict[str, object]:
        return self._request("GET", "/v1/server/status")

    def action(self, action: str) -> dict[str, object]:
        return self._request("POST", "/v1/server/actions", {"action": action})

    def settings(self) -> dict[str, object]:
        return dict(self._request("GET", "/v1/server/settings").get("settings") or {})

    def save_settings(self, settings: dict[str, object]) -> dict[str, object]:
        response = self._request("PUT", "/v1/server/settings", {"settings": settings})
        return dict(response.get("settings") or {})

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> dict[str, object]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            f"{self.server_url}{path}", data=data, method=method
        )
        request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            try:
                message = json.loads(exc.read().decode("utf-8")).get("error")
            except (ValueError, json.JSONDecodeError):
                message = None
            raise IndexServerError(str(message or exc)) from exc
        except (OSError, urllib.error.URLError, ValueError) as exc:
            raise IndexServerError(str(exc)) from exc
