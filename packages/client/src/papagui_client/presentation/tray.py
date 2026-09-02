"""Server tray presentation policy separated from Qt widgets."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping


class TrayHealth(StrEnum):
    OFFLINE = "offline"
    PROBLEM = "problem"
    ONLINE = "online"
    RUNNING = "running"


@dataclass(frozen=True, slots=True)
class TrayStatusViewModel:
    health: TrayHealth
    badge: str
    color: str
    summary: str
    details: str
    running: bool = False


@dataclass(frozen=True, slots=True)
class TrayActivityViewModel:
    """One deduplicatable, human-readable observation of an index run."""

    key: tuple[str, ...]
    text: str


class TrayPresenter:
    _FRAMES = ("◔", "◑", "◕", "◐")

    def __init__(self) -> None:
        self._frame = 0

    def offline(self, error: str) -> TrayStatusViewModel:
        return TrayStatusViewModel(
            TrayHealth.OFFLINE,
            "● OFFLINE",
            "#d64b4b",
            f"Server nicht erreichbar · {error}",
            "Keine aktuellen Serverdaten verfügbar.",
        )

    def status(self, payload: Mapping[str, object]) -> TrayStatusViewModel:
        server_state = str(payload.get("state", "online")).lower()
        raw_index = payload.get("index")
        index = raw_index if isinstance(raw_index, Mapping) else {}
        index_state = str(index.get("state", "idle")).lower()
        running = index_state in {"running", "starting", "cancelling"}
        problem = server_state in {"error", "degraded", "problem"} or index_state in {
            "error",
            "failed",
        }
        if problem:
            health, badge, color = TrayHealth.PROBLEM, "● PROBLEM", "#e58b27"
        elif running:
            health, badge, color = TrayHealth.RUNNING, self.next_running_badge(), "#269d69"
        else:
            health, badge, color = TrayHealth.ONLINE, "● ONLINE", "#269d69"
        progress = index.get("progress") if isinstance(index.get("progress"), Mapping) else {}
        message = str(index.get("message") or payload.get("message") or index_state)
        return TrayStatusViewModel(
            health=health,
            badge=badge,
            color=color,
            summary=f"Server: {server_state} · Indexjob: {message}",
            details=(
                f"Serverversion: {payload.get('server_version', '–')}\n"
                f"Indexgeneration: {payload.get('active_index_generation', '–')}\n"
                f"Kundengeneration: {payload.get('active_customer_generation', '–')}\n"
                f"Fortschritt: {progress.get('processed_items', 0)} / "
                f"{progress.get('total_items', 0)}"
            ),
            running=running,
        )

    def next_running_badge(self) -> str:
        value = f"{self._FRAMES[self._frame]} INDEXLAUF"
        self._frame = (self._frame + 1) % len(self._FRAMES)
        return value

    @staticmethod
    def activity(payload: Mapping[str, object]) -> TrayActivityViewModel:
        raw_index = payload.get("index")
        index = raw_index if isinstance(raw_index, Mapping) else {}
        raw_progress = index.get("progress")
        progress = raw_progress if isinstance(raw_progress, Mapping) else {}
        state = str(index.get("state") or "idle")
        phase = str(progress.get("phase") or index.get("phase") or "")
        processed = progress.get("processed_items", index.get("processed_count"))
        total = progress.get("total_items")
        message = str(index.get("message") or index.get("error") or "")
        timestamp = str(
            index.get("finished_at")
            or index.get("completed_at")
            or index.get("started_at")
            or index.get("updated_at")
            or payload.get("observed_at")
            or payload.get("updated_at")
            or ""
        )
        progress_text = ""
        if processed is not None:
            progress_text = str(processed)
            if total is not None:
                progress_text += f" / {total}"
        values = []
        for value in (timestamp, state, phase, progress_text, message):
            if value and value not in values:
                values.append(value)
        key = tuple(
            str(value or "")
            for value in (
                index.get("run_id"),
                state,
                phase,
                processed,
                total,
                message,
                index.get("finished_at") or index.get("completed_at"),
            )
        )
        return TrayActivityViewModel(key, " · ".join(values) or "Indexstatus aktualisiert")

    @staticmethod
    def interval_fields(seconds: int) -> tuple[int, str]:
        seconds = max(900, min(172_800, seconds))
        return (seconds // 3600, "Stunden") if seconds % 3600 == 0 else (seconds // 60, "Minuten")

    @staticmethod
    def interval_seconds(value: int, unit: str) -> int:
        seconds = value * (3600 if unit == "Stunden" else 60)
        if not 900 <= seconds <= 172_800:
            raise ValueError("Intervall muss zwischen 15 Minuten und 48 Stunden liegen")
        return seconds
