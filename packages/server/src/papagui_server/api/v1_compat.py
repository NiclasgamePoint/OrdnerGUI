"""Read-mostly v1 compatibility routes; destructive actions still require admin."""

from __future__ import annotations

from typing import Any
import uuid

from fastapi import Depends, FastAPI, Header
from fastapi.responses import JSONResponse, StreamingResponse

from papagui_server.api.http import (
    archive_response,
    expected_revision,
    mapping,
    mutation_response,
)
from papagui_server.composition import ServerContainer
from papagui_server.domain.errors import ResourceNotFoundError


def install_v1_routes(
    app: FastAPI,
    container: ServerContainer,
    require_client: Any,
    require_admin: Any,
) -> None:
    @app.get("/v1/server/status", dependencies=[Depends(require_client)], deprecated=True)
    async def legacy_status() -> dict[str, Any]:
        current = container.coordinator.status()
        index = dict(current.get("index") or {})
        progress = dict(index.get("progress") or {})
        settings = container.settings.get()
        return {
            "server": {
                "status": current["state"],
                "version": current["server_version"],
                "interval_seconds": settings.interval_seconds,
                "source": str(container.configuration.source_path),
            },
            "job": {
                "status": index.get("state", "idle"),
                "job_id": index.get("run_id"),
                "phase": progress.get("phase", ""),
                "processed_count": progress.get("processed_items", 0),
                "current_path": (progress.get("current_source") or {}).get(
                    "relative_path", ""
                ),
                "error": index.get("message") or None,
            },
            "generation": container.publisher.current() or {},
            "backups": sum((current.get("backups") or {}).values()),
        }

    @app.get("/v1/server/settings", dependencies=[Depends(require_client)], deprecated=True)
    async def legacy_settings() -> dict[str, Any]:
        values = container.settings.get().to_dict()
        values["automatic_monitoring_enabled"] = values["automatic_runs_enabled"]
        return {"settings": values}

    @app.put("/v1/server/settings", dependencies=[Depends(require_admin)], deprecated=True)
    async def update_legacy_settings(payload: dict[str, Any]) -> dict[str, Any]:
        values = mapping(payload.get("settings", payload), "settings")
        if "automatic_monitoring_enabled" in values:
            values["automatic_runs_enabled"] = values.pop(
                "automatic_monitoring_enabled"
            )
        return {"settings": container.settings.update(values).to_dict()}

    @app.post(
        "/v1/server/actions",
        status_code=202,
        dependencies=[Depends(require_admin)],
        deprecated=True,
    )
    async def legacy_action(payload: dict[str, Any]) -> dict[str, Any]:
        action = str(payload.get("action", ""))
        if action == "run":
            return container.index_admin.start()
        if action == "rebuild":
            return container.index_admin.start(full_rebuild=True)
        if action == "delete":
            return container.index_admin.delete(rebuild=True)
        if action == "cancel":
            return container.index_admin.cancel()
        if action == "restart":
            return container.index_admin.restart()
        if action.startswith("interval:"):
            interval = int(float(action.partition(":")[2]))
            return {
                "accepted": True,
                "settings": container.settings.update(
                    {"interval_seconds": interval}
                ).to_dict(),
            }
        raise ValueError("Unbekannte Indexaktion.")

    @app.get("/v1/index/current", dependencies=[Depends(require_client)], deprecated=True)
    async def legacy_current() -> dict[str, Any]:
        current = container.publisher.current() or {}
        index = dict((current.get("components") or {}).get("index") or {})
        if not index:
            raise ResourceNotFoundError("Es ist noch keine Indexgeneration vorhanden.")
        return {
            "schema_version": 1,
            "generation": index["generation"],
            "created_at": index["created_at"],
            "archive": f"{index['generation']}.zip",
            "size": index["size"],
            "sha256": index["sha256"],
        }

    @app.get(
        "/v1/index/generations/{archive_name}",
        dependencies=[Depends(require_client)],
        deprecated=True,
    )
    async def legacy_archive(archive_name: str) -> StreamingResponse:
        generation = archive_name.removesuffix(".zip")
        path = container.publisher.archive_path("index", generation)
        return archive_response(path)

    @app.get(
        "/v1/customers/{customer_id}",
        dependencies=[Depends(require_client)],
        deprecated=True,
    )
    async def legacy_customer(customer_id: int) -> dict[str, Any]:
        return {"customer": container.customers.get_customer(customer_id)}

    @app.post("/v1/customers", dependencies=[Depends(require_client)], deprecated=True)
    async def legacy_create_customer(payload: dict[str, Any]) -> JSONResponse:
        result = container.customers.create_customer(
            mapping(payload.get("customer", payload), "customer"),
            idempotency_key=f"legacy-{uuid.uuid4()}",
        )
        return mutation_response(result)

    @app.put(
        "/v1/customers/{customer_id}",
        dependencies=[Depends(require_client)],
        deprecated=True,
    )
    async def legacy_update_customer(
        customer_id: int, payload: dict[str, Any]
    ) -> JSONResponse:
        result = container.customers.update_customer(
            customer_id,
            mapping(payload.get("customer", payload), "customer"),
            expected_revision=expected_revision(payload.get("expected_revision"), None),
            idempotency_key=f"legacy-{uuid.uuid4()}",
        )
        return mutation_response(result)

    @app.delete(
        "/v1/customers/{customer_id}",
        dependencies=[Depends(require_admin)],
        deprecated=True,
    )
    async def legacy_delete_customer(
        customer_id: int,
        if_match: str | None = Header(default=None, alias="If-Match"),
    ) -> JSONResponse:
        return mutation_response(
            container.customers.delete_customer(
                customer_id,
                expected_revision=expected_revision(None, if_match),
                idempotency_key=f"legacy-{uuid.uuid4()}",
            )
        )
