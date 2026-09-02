"""Client-token authentication and process-local administrative sessions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hmac
import json
from pathlib import Path
import secrets
import threading

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from papagui_server.adapters.generations import atomic_json
from papagui_server.domain.errors import AdminAuthenticationError


@dataclass(frozen=True, slots=True)
class SecurityConfiguration:
    client_token: str
    admin_password_hash: str
    allow_insecure_no_client_token: bool = False
    admin_session_minutes: int = 480


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


class AdminSessionManager:
    """Verify Argon2id and retain opaque sessions only in process memory."""

    def __init__(
        self,
        password_hash: str,
        *,
        ttl: timedelta = timedelta(hours=8),
        password_hasher: PasswordHasher | None = None,
    ) -> None:
        self._password_hash = password_hash
        self._ttl = ttl
        self._hasher = password_hasher or PasswordHasher()
        self._sessions: dict[str, datetime] = {}
        self._lock = threading.RLock()

    @property
    def configured(self) -> bool:
        return bool(self._password_hash)

    def login(self, password: str) -> tuple[str, str]:
        if not self._password_hash:
            raise AdminAuthenticationError("Es ist kein Adminpasswort konfiguriert.")
        try:
            valid = self._hasher.verify(self._password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError) as error:
            raise AdminAuthenticationError("Das Adminpasswort ist ungültig.") from error
        if not valid:
            raise AdminAuthenticationError("Das Adminpasswort ist ungültig.")
        token = secrets.token_urlsafe(32)
        expires = datetime.now(timezone.utc) + self._ttl
        with self._lock:
            self._purge_locked()
            self._sessions[token] = expires
        return token, expires.isoformat()

    def accepts(self, token: str | None) -> bool:
        if not token:
            return False
        now = datetime.now(timezone.utc)
        with self._lock:
            self._purge_locked(now)
            expires = self._sessions.get(token)
            return expires is not None and expires > now

    def revoke(self, token: str) -> None:
        with self._lock:
            self._sessions.pop(token, None)

    def replace_password_hash(self, password_hash: str) -> None:
        with self._lock:
            self._password_hash = password_hash
            self._sessions.clear()

    def _purge_locked(self, now: datetime | None = None) -> None:
        now = now or datetime.now(timezone.utc)
        for token, expires in tuple(self._sessions.items()):
            if expires <= now:
                self._sessions.pop(token, None)


class SecurityConfigurationStore:
    """Keep only the password hash in the persistent config volume."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def admin_password_hash(self) -> str:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return str(payload.get("admin_password_hash", ""))
        except (OSError, ValueError, TypeError):
            return ""

    def save_admin_password_hash(self, password_hash: str) -> None:
        if not password_hash.startswith("$argon2id$"):
            raise ValueError("Das Adminpasswort muss als Argon2id-Hash gespeichert werden.")
        atomic_json(self.path, {"admin_password_hash": password_hash})

    def initialize(
        self,
        *,
        configured_hash: str = "",
        bootstrap_password: str = "",
    ) -> str:
        existing = self.admin_password_hash()
        candidate = configured_hash.strip() or existing
        if candidate:
            if not candidate.startswith("$argon2id$"):
                raise ValueError("PAPAGUI_ADMIN_PASSWORD_HASH ist kein Argon2id-Hash.")
            if candidate != existing:
                self.save_admin_password_hash(candidate)
            return candidate
        if bootstrap_password:
            generated = PasswordHasher().hash(bootstrap_password)
            self.save_admin_password_hash(generated)
            return generated
        return ""


def hash_admin_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Das Adminpasswort muss mindestens 12 Zeichen lang sein.")
    return PasswordHasher().hash(password)
