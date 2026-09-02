"""Server and index-run status contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from ._base import (
    ContractValidationError,
    JsonDto,
    JsonValue,
    mapping_get,
    optional_string,
    require_int,
    require_mapping,
    require_string,
)
from .generations import SourcePath


class ServerState(str, Enum):
    STARTING = "starting"
    ONLINE = "online"
    DEGRADED = "degraded"
    STOPPING = "stopping"
    OFFLINE = "offline"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, value: object) -> "ServerState":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).strip().casefold())
        except ValueError:
            return cls.UNKNOWN


class IndexRunState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RUNNING = "running"
    READY = "ready"
    PAUSED = "paused"
    COMPLETED = "completed"
    NO_CHANGES = "no_changes"
    CANCELLED = "cancelled"
    ERROR = "error"
    UNKNOWN = "unknown"

    @classmethod
    def parse(cls, value: object) -> "IndexRunState":
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().casefold().replace(" ", "_")
        aliases = {"ok": cls.COMPLETED, "failed": cls.ERROR, "canceled": cls.CANCELLED}
        if normalized in aliases:
            return aliases[normalized]
        try:
            return cls(normalized)
        except ValueError:
            return cls.UNKNOWN


ACTIVE_INDEX_STATES = frozenset(
    {IndexRunState.STARTING, IndexRunState.RUNNING, IndexRunState.READY}
)


@dataclass(frozen=True, slots=True)
class IndexProgress(JsonDto):
    processed_items: int = 0
    total_items: int = 0
    failed_items: int = 0
    phase: str = ""
    current_source: SourcePath | None = None
    legacy_current_path: str | None = None

    def __post_init__(self) -> None:
        require_int(self.processed_items, "processed_items", minimum=0)
        require_int(self.total_items, "total_items", minimum=0)
        require_int(self.failed_items, "failed_items", minimum=0)
        require_string(self.phase, "phase", allow_empty=True)
        optional_string(self.legacy_current_path, "legacy_current_path")
        if self.current_source is not None and not isinstance(self.current_source, SourcePath):
            raise ContractValidationError("current_source must be a SourcePath DTO")

    @property
    def fraction(self) -> float | None:
        if self.total_items == 0:
            return None
        return min(1.0, self.processed_items / self.total_items)

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "IndexProgress":
        payload = require_mapping(payload, "index_progress")
        raw_current = mapping_get(
            payload, "current_source", mapping_get(payload, "current_path", None)
        )
        current_source = None
        legacy_path = None
        if isinstance(raw_current, Mapping):
            current_source = SourcePath.from_dict(raw_current)
        elif raw_current:
            legacy_path = require_string(raw_current, "current_path")
        processed = mapping_get(
            payload,
            "processed_items",
            mapping_get(
                payload,
                "processed_count",
                mapping_get(
                    payload,
                    "indexed_count",
                    mapping_get(payload, "completed_documents", 0),
                ),
            ),
        )
        total = mapping_get(
            payload,
            "total_items",
            mapping_get(
                payload,
                "total_count",
                mapping_get(payload, "total_documents", 0),
            ),
        )
        failed = mapping_get(
            payload,
            "failed_items",
            mapping_get(
                payload,
                "failed_count",
                mapping_get(payload, "failed_documents", 0),
            ),
        )
        return cls(
            processed_items=require_int(processed, "processed_items", minimum=0),
            total_items=require_int(total, "total_items", minimum=0),
            failed_items=require_int(failed, "failed_items", minimum=0),
            phase=require_string(mapping_get(payload, "phase", ""), "phase", allow_empty=True),
            current_source=current_source,
            legacy_current_path=legacy_path,
        )


@dataclass(frozen=True, slots=True)
class IndexStatus(JsonDto):
    state: IndexRunState = IndexRunState.IDLE
    progress: IndexProgress = field(default_factory=IndexProgress)
    run_id: str | None = None
    message: str | None = None
    started_at: str | None = None
    updated_at: str | None = None
    finished_at: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, IndexRunState):
            object.__setattr__(self, "state", IndexRunState.parse(self.state))
        if not isinstance(self.progress, IndexProgress):
            raise ContractValidationError("progress must be an IndexProgress DTO")
        optional_string(self.run_id, "run_id")
        optional_string(self.message, "message")
        optional_string(self.started_at, "started_at")
        optional_string(self.updated_at, "updated_at")
        optional_string(self.finished_at, "finished_at")

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_INDEX_STATES

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "IndexStatus":
        payload = require_mapping(payload, "index_status")
        raw_progress = mapping_get(payload, "progress", payload)
        message = mapping_get(payload, "message", mapping_get(payload, "error", None))
        # v1 emitted empty strings for successful jobs. In v2 optional strings are
        # either meaningful text or null, so normalize at the compatibility edge.
        if isinstance(message, str) and not message.strip():
            message = None
        return cls(
            state=IndexRunState.parse(
                mapping_get(payload, "state", mapping_get(payload, "status", "idle"))
            ),
            progress=IndexProgress.from_dict(
                require_mapping(raw_progress, "index_status.progress")
            ),
            run_id=optional_string(mapping_get(payload, "run_id", None), "run_id"),
            message=optional_string(message, "message"),
            started_at=optional_string(mapping_get(payload, "started_at", None), "started_at"),
            updated_at=optional_string(mapping_get(payload, "updated_at", None), "updated_at"),
            finished_at=optional_string(mapping_get(payload, "finished_at", None), "finished_at"),
        )


@dataclass(frozen=True, slots=True)
class ServerStatus(JsonDto):
    state: ServerState
    index: IndexStatus = field(default_factory=IndexStatus)
    server_version: str = "unknown"
    uptime_seconds: int = 0
    observed_at: str | None = None
    active_index_generation: str | None = None
    active_customer_generation: str | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.state, ServerState):
            object.__setattr__(self, "state", ServerState.parse(self.state))
        if not isinstance(self.index, IndexStatus):
            raise ContractValidationError("index must be an IndexStatus DTO")
        require_string(self.server_version, "server_version")
        require_int(self.uptime_seconds, "uptime_seconds", minimum=0)
        optional_string(self.observed_at, "observed_at")
        optional_string(self.active_index_generation, "active_index_generation")
        optional_string(self.active_customer_generation, "active_customer_generation")
        optional_string(self.message, "message")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "state": self.state.value,
            "server_version": self.server_version,
            "uptime_seconds": self.uptime_seconds,
            "observed_at": self.observed_at,
            "active_index_generation": self.active_index_generation,
            "active_customer_generation": self.active_customer_generation,
            "message": self.message,
            "index": self.index.to_dict(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "ServerStatus":
        payload = require_mapping(payload, "server_status")
        # v1 used {server: {...}, job: {...}, generation: {...}}.
        raw_server = mapping_get(payload, "server", payload)
        server = require_mapping(raw_server, "server_status.server")
        raw_index = mapping_get(payload, "index", mapping_get(payload, "job", {}))
        generation = mapping_get(payload, "generation", {})
        generation_payload = (
            require_mapping(generation, "generation") if isinstance(generation, Mapping) else {}
        )
        legacy_generation = mapping_get(generation_payload, "generation", None)
        return cls(
            state=ServerState.parse(
                mapping_get(server, "state", mapping_get(server, "status", "unknown"))
            ),
            index=IndexStatus.from_dict(require_mapping(raw_index, "server_status.index")),
            server_version=require_string(
                mapping_get(
                    server,
                    "server_version",
                    mapping_get(server, "version", "unknown"),
                ),
                "server_version",
            ),
            uptime_seconds=require_int(
                mapping_get(server, "uptime_seconds", 0),
                "uptime_seconds",
                minimum=0,
            ),
            observed_at=optional_string(mapping_get(payload, "observed_at", None), "observed_at"),
            active_index_generation=optional_string(
                mapping_get(payload, "active_index_generation", legacy_generation),
                "active_index_generation",
            ),
            active_customer_generation=optional_string(
                mapping_get(payload, "active_customer_generation", legacy_generation),
                "active_customer_generation",
            ),
            message=optional_string(
                mapping_get(server, "message", mapping_get(server, "error", None)),
                "message",
            ),
        )


IndexRunStatus = IndexStatus
