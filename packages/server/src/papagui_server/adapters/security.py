"""Client-token authentication for the server API."""

from __future__ import annotations

import hmac


class ClientTokenAuthenticator:
    def __init__(self, token: str, *, allow_insecure: bool = False) -> None:
        self._token = token
        self._allow_insecure = allow_insecure
        if not token and not allow_insecure:
            raise ValueError(
                "PAPAGUI_API_TOKEN fehlt. Unsicherer Betrieb muss explizit erlaubt werden."
            )

    def accepts(self, token: str | None) -> bool:
        if not self._token:
            return self._allow_insecure
        return bool(token) and hmac.compare_digest(self._token, str(token))
