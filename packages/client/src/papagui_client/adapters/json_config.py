"""Atomic, versioned persistence for desktop-only configuration."""

from __future__ import annotations

import json
import os
from pathlib import Path
import getpass
import subprocess
from typing import Mapping
import uuid
import warnings

from papagui_client.config import ClientSettings, ResolvedClientConfiguration


CLIENT_CONFIG_SCHEMA_VERSION = 2


class ClientConfigError(RuntimeError):
    pass


class ClientConfigSecurityWarning(RuntimeWarning):
    """The configuration was saved, but its Windows ACL could not be hardened."""


class JsonClientConfigRepository:
    """Store one JSON document; environment overrides are resolved separately."""

    def __init__(
        self,
        path: Path,
        *,
        data_root: Path | None = None,
        strict_permissions: bool = False,
    ):
        self.path = path.expanduser().resolve()
        self.data_root = (data_root or self.path.parent).expanduser().resolve()
        self.strict_permissions = strict_permissions

    def load(self) -> ClientSettings:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return ClientSettings(data_root=self.data_root)
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            raise ClientConfigError(f"Clientkonfiguration ist ungültig: {exc}") from exc
        if not isinstance(raw, Mapping):
            raise ClientConfigError("Clientkonfiguration muss ein JSON-Objekt sein")
        try:
            payload, migrated = self._migrate(raw)
            settings = ClientSettings.from_dict(payload, data_root=self.data_root)
        except (TypeError, ValueError) as exc:
            raise ClientConfigError(f"Clientkonfiguration ist ungültig: {exc}") from exc
        if migrated:
            self.save(settings)
        return settings

    def resolve(
        self, environment: Mapping[str, str] | None = None
    ) -> ResolvedClientConfiguration:
        return self.load().with_environment(environment)

    def save(self, settings: ClientSettings) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
        payload = {
            "schema_version": CLIENT_CONFIG_SCHEMA_VERSION,
            **settings.to_dict(),
        }
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            self._protect_secret_file(temporary)
            os.replace(temporary, self.path)
        except OSError as exc:
            raise ClientConfigError(f"Clientkonfiguration konnte nicht gespeichert werden: {exc}") from exc
        finally:
            temporary.unlink(missing_ok=True)

    def _protect_secret_file(self, path: Path) -> None:
        if os.name != "nt":
            path.chmod(0o600)
            return
        identity = os.environ.get("USERNAME") or getpass.getuser()
        message = ""
        try:
            result = subprocess.run(
                [
                    "icacls",
                    str(path),
                    "/inheritance:r",
                    "/grant:r",
                    f"{identity}:(F)",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode:
                details = (result.stderr or result.stdout).strip()
                message = f"Windows-ACL konnte nicht gesetzt werden: {details or result.returncode}"
        except (OSError, subprocess.SubprocessError) as exc:
            message = f"Windows-ACL konnte nicht gesetzt werden: {exc}"
        if not message:
            return
        if self.strict_permissions:
            raise ClientConfigError(message)
        warnings.warn(message, ClientConfigSecurityWarning, stacklevel=2)

    @staticmethod
    def _migrate(payload: Mapping[str, object]) -> tuple[dict[str, object], bool]:
        schema = payload.get("schema_version", 1)
        if schema == CLIENT_CONFIG_SCHEMA_VERSION:
            return dict(payload), False
        if schema != 1:
            raise ValueError(f"nicht unterstützte Konfigurationsversion: {schema}")
        mappings = payload.get("source_mappings", payload.get("source_paths", {}))
        if isinstance(mappings, Mapping):
            migrated_mappings = {}
            for source_id, roots in mappings.items():
                if isinstance(roots, Mapping):
                    migrated_mappings[str(source_id)] = dict(roots)
                elif isinstance(roots, str):
                    migrated_mappings[str(source_id)] = {"linux": roots}
        else:
            migrated_mappings = {}
        interval = payload.get("sync_interval_seconds")
        if interval is None:
            interval = int(payload.get("sync_interval_minutes", 15)) * 60
        return (
            {
                "schema_version": CLIENT_CONFIG_SCHEMA_VERSION,
                "server_url": payload.get(
                    "server_url", payload.get("index_server_url", "http://127.0.0.1:8765")
                ),
                "api_token": payload.get("api_token", payload.get("token", "")),
                "source_mappings": migrated_mappings,
                "timeout_seconds": payload.get("timeout_seconds", 15),
                "sync_interval_seconds": interval,
                "theme": payload.get("theme", "system"),
            },
            True,
        )
