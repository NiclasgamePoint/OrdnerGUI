"""Persistent source identity guard for unattended NAS mount safety."""

from __future__ import annotations

import json
import os
from pathlib import Path
import uuid

from papagui_server.domain.errors import SourceUnavailableError


_IGNORED_ROOT_ENTRIES = {
    ".ds_store",
    "thumbs.db",
    "desktop.ini",
    "lost+found",
    "@eadir",
    "#recycle",
}


class PersistentSourceIdentityGuard:
    """Reject empty/replaced mounts using portable persisted root sentinels."""

    def __init__(self, state_path: Path, *, allow_initialize: bool) -> None:
        self._state_path = state_path
        self._allow_initialize = allow_initialize

    def probe(self, source_path: Path, source_id: str) -> tuple[bool, str]:
        if not source_path.is_dir():
            return False, "Datenquelle nicht erreichbar."
        entries = self._entries(source_path)
        if not entries:
            return False, "Datenquelle ist leer; ein fehlender NAS-Mount wird vermutet."
        state, state_error = self._load()
        if state_error:
            return False, state_error
        if state is None:
            if not self._allow_initialize:
                return False, "Quellidentität ist noch nicht initialisiert."
            return True, ""
        if state.get("source_id") != source_id:
            return False, "Die konfigurierte source_id passt nicht zur Quellidentität."
        sentinels = {
            str(item) for item in state.get("sentinel_entries", ()) if str(item)
        }
        if not sentinels or sentinels.isdisjoint(entries):
            return False, "Der Mountinhalt passt nicht zur gespeicherten Quellidentität."
        return True, ""

    def ensure_ready(self, source_path: Path, source_id: str) -> None:
        available, message = self.probe(source_path, source_id)
        if not available:
            raise SourceUnavailableError(message)
        state, _state_error = self._load()
        if state is None:
            self._write(
                {
                    "schema_version": 1,
                    "source_id": source_id,
                    "sentinel_entries": sorted(self._entries(source_path))[:32],
                }
            )

    @staticmethod
    def _entries(source_path: Path) -> set[str]:
        try:
            return {
                entry.name.casefold()
                for entry in source_path.iterdir()
                if entry.name.casefold() not in _IGNORED_ROOT_ENTRIES
            }
        except OSError:
            return set()

    def _load(self) -> tuple[dict[str, object] | None, str]:
        if not self._state_path.exists():
            return None, ""
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return None, "Die gespeicherte Quellidentität ist nicht lesbar."
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            return None, "Die gespeicherte Quellidentität ist ungültig."
        return payload, ""

    def _write(self, payload: dict[str, object]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_name(
            f".{self._state_path.name}.{uuid.uuid4().hex}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            os.replace(temporary, self._state_path)
        finally:
            temporary.unlink(missing_ok=True)
