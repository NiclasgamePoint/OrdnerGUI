"""Versioned FastAPI transport and deliberately narrow v1 compatibility layer."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from papagui_contracts import (
    Capabilities,
    ContractValidationError,
    MutationEnvelope,
    MutationOperation,
    MutationTarget,
    SystemInfo,
    __version__ as contracts_version,
)

from papagui_server import __version__
import papagui_server.api.schemas as api_models
from papagui_server.api.http import (
    archive_response as _archive_response,
    error_response as _error,
    expected_revision as _expected_revision,
    idempotency_key as _idempotency_key,
    mutation_response as _mutation_response,
)
from papagui_server.api.v1_compat import install_v1_routes
from papagui_server.composition import ServerContainer
from papagui_server.domain.errors import (
    CustomerConflictError,
    IdempotencyConflictError,
    ProjectAssignmentConflictError,
    ResourceBusyError,
    ResourceNotFoundError,
    SuggestionOverwriteError,
)


def _job_finished_after_run(job: dict[str, Any], last_run_at: str) -> bool:
    """Do not let an older failed recheck mask a later successful index run."""
    if not last_run_at:
        return True
    try:
        finished = datetime.fromisoformat(str(job.get("finished_at", "")).replace("Z", "+00:00"))
        evaluated = datetime.fromisoformat(last_run_at.replace("Z", "+00:00"))
        finished = finished if finished.tzinfo else finished.replace(tzinfo=timezone.utc)
        evaluated = evaluated if evaluated.tzinfo else evaluated.replace(tzinfo=timezone.utc)
        return finished >= evaluated
    except (ValueError, TypeError):
        # An unknown timestamp is not evidence of a newer successful evaluation.
        return True


def create_app(
    container: ServerContainer,
    *,
    manage_lifecycle: bool = True,
    run_on_start: bool = True,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if manage_lifecycle:
            container.coordinator.start(run_on_start=run_on_start)
            container.recognition_jobs.start()
        try:
            yield
        finally:
            if manage_lifecycle:
                container.recognition_jobs.stop()
                container.coordinator.stop()

    app = FastAPI(
        title="PapaGUI Server API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url=None,
        responses={
            400: {"model": api_models.ErrorResponse, "description": "Invalid request"},
            401: {"model": api_models.ErrorResponse, "description": "Client authentication required"},
            404: {"model": api_models.ErrorResponse, "description": "Resource not found"},
            409: {"model": api_models.ErrorResponse, "description": "Concurrent or state conflict"},
            422: {"model": api_models.ErrorResponse, "description": "Request validation failed"},
        },
    )
    app.state.container = container
    client_bearer = HTTPBearer(auto_error=False)

    async def require_client(
        credentials: HTTPAuthorizationCredentials | None = Depends(client_bearer),
    ) -> None:
        token = credentials.credentials if credentials is not None else None
        if credentials is not None and credentials.scheme.casefold() != "bearer":
            token = None
        if not container.client_auth.accepts(token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Client-Authentifizierung erforderlich.",
                headers={"WWW-Authenticate": "Bearer"},
            )

    @app.exception_handler(CustomerConflictError)
    async def customer_conflict(
        _request: Request, error: CustomerConflictError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "customer_revision_conflict",
                    "message": str(error),
                    "status": 409,
                },
                "current": error.current,
            },
        )

    @app.exception_handler(IdempotencyConflictError)
    async def idempotency_conflict(
        _request: Request, error: IdempotencyConflictError
    ) -> JSONResponse:
        return _error(409, "idempotency_conflict", str(error))

    @app.exception_handler(ResourceNotFoundError)
    async def resource_not_found(
        _request: Request, error: ResourceNotFoundError
    ) -> JSONResponse:
        return _error(404, "not_found", str(error))

    @app.exception_handler(ResourceBusyError)
    async def resource_busy(_request: Request, error: ResourceBusyError) -> JSONResponse:
        return _error(409, "resource_busy", str(error))

    @app.exception_handler(ProjectAssignmentConflictError)
    async def project_assignment_conflict(
        _request: Request, error: ProjectAssignmentConflictError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "project_assignment_conflict",
                    "message": str(error),
                    "status": 409,
                },
                "current": container.customers.get_customer(
                    error.current_customer_id
                ),
            },
        )

    @app.exception_handler(SuggestionOverwriteError)
    async def suggestion_overwrite(
        _request: Request, error: SuggestionOverwriteError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "manual_value_conflict",
                    "message": str(error),
                    "status": 409,
                },
                "current": error.current,
            },
        )

    @app.exception_handler(ValueError)
    @app.exception_handler(ContractValidationError)
    async def invalid_request(_request: Request, error: Exception) -> JSONResponse:
        return _error(400, "invalid_request", str(error))

    @app.exception_handler(RequestValidationError)
    async def request_validation(
        request: Request, error: RequestValidationError
    ) -> JSONResponse:
        customer_mutation = request.method in {"POST", "PUT", "PATCH"} and (
            request.url.path == "/v2/customer-mutations"
            or request.url.path.startswith("/v2/customers")
        )
        if request.url.path == "/v2/admin/settings" or customer_mutation:
            return _error(400, "invalid_request", str(error.errors()[0]["msg"]))
        return _error(422, "validation_error", str(error.errors()[0]["msg"]))

    @app.get("/health", tags=["system"], response_model=api_models.HealthResponse)
    async def health() -> dict[str, Any]:
        state = container.coordinator.status()
        return {
            "status": "ok" if state["state"] == "online" else "degraded",
            "server_version": __version__,
        }

    @app.get("/v2/system/info", dependencies=[Depends(require_client)], tags=["system"], response_model=api_models.SystemInfoResponse)
    async def system_info() -> dict[str, Any]:
        return SystemInfo(
            server_version=__version__,
            contracts_version=contracts_version,
            capabilities=Capabilities(),
        ).to_dict()

    @app.get("/v2/server/status", dependencies=[Depends(require_client)], tags=["system"], response_model=api_models.ServerStatusResponse)
    async def server_status() -> dict[str, Any]:
        return container.coordinator.status()

    @app.get("/v2/admin/settings", dependencies=[Depends(require_client)], tags=["admin"], response_model=api_models.SettingsResponse)
    async def get_admin_settings() -> dict[str, Any]:
        return {"settings": container.settings.get().to_dict()}

    @app.put("/v2/admin/settings", dependencies=[Depends(require_client)], tags=["admin"], response_model=api_models.SettingsResponse)
    async def update_admin_settings(payload: api_models.SettingsUpdateRequest) -> dict[str, Any]:
        return {
            "settings": container.settings.update(
                payload.settings.model_dump(exclude_none=True)
            ).to_dict()
        }

    @app.post("/v2/admin/index-runs", status_code=202, dependencies=[Depends(require_client)], tags=["admin"], response_model=api_models.ActionResponse)
    async def start_index_run(payload: api_models.IndexRunRequest | None = None) -> dict[str, Any]:
        return container.index_admin.start(
            full_rebuild=payload.full_rebuild if payload is not None else False
        )

    @app.post("/v2/admin/index-runs/current/cancel", status_code=202, dependencies=[Depends(require_client)], tags=["admin"], response_model=api_models.ActionResponse)
    async def cancel_index_run() -> dict[str, Any]:
        return container.index_admin.cancel()

    @app.delete("/v2/admin/index", status_code=202, dependencies=[Depends(require_client)], tags=["admin"], response_model=api_models.ActionResponse)
    async def delete_index(rebuild: bool = Query(default=True)) -> dict[str, Any]:
        return container.index_admin.delete(rebuild=rebuild)

    @app.post("/v2/admin/server/restart", status_code=202, dependencies=[Depends(require_client)], tags=["admin"], response_model=api_models.ActionResponse)
    async def restart_server() -> dict[str, Any]:
        return container.index_admin.restart()

    @app.get("/v2/generations/current", dependencies=[Depends(require_client)], tags=["generations"], response_model=api_models.GenerationManifestResponse)
    async def current_generation() -> dict[str, Any]:
        current = container.publisher.current()
        components = dict((current or {}).get("components") or {})
        if not current or not components.get("index") or not components.get("customers"):
            raise ResourceNotFoundError("Es ist noch keine vollständige Generation vorhanden.")
        return current

    @app.get("/v2/generations/{component}/{generation}/manifest", dependencies=[Depends(require_client)], tags=["generations"], response_model=api_models.GenerationComponentModel)
    async def generation_manifest(component: str, generation: str) -> dict[str, Any]:
        return container.publisher.descriptor(component, generation)

    @app.get("/v2/generations/{component}/{generation}/archive", dependencies=[Depends(require_client)], tags=["generations"])
    async def generation_archive(component: str, generation: str) -> StreamingResponse:
        path = container.publisher.archive_path(component, generation)
        return _archive_response(path)

    @app.get("/v2/catalog/search", dependencies=[Depends(require_client)], tags=["catalog"], response_model=api_models.CatalogSearchResponse)
    async def catalog_search(
        source_id: str = Query(default=container.configuration.source_id),
        query: str = Query(default=""),
        domain_folder: str = Query(default=""),
        time_bucket: str = Query(default=""),
        file_type: str = Query(default=""),
        project_root_id: int | None = Query(default=None, ge=1),
        sort: str = Query(default="name"),
        limit: int = Query(default=100, ge=1, le=2_000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        return container.catalog.search(
            source_id=source_id,
            query=query,
            domain_folder=domain_folder,
            time_bucket=time_bucket,
            file_type=file_type,
            project_root_id=project_root_id,
            sort=sort,
            limit=limit,
            offset=offset,
        )

    @app.get("/v2/catalog/facets", dependencies=[Depends(require_client)], tags=["catalog"], response_model=api_models.CatalogFacetsResponse)
    async def catalog_facets(
        source_id: str = Query(default=container.configuration.source_id),
    ) -> dict[str, Any]:
        return container.catalog.facets(source_id=source_id)

    @app.get("/v2/catalog/folders", dependencies=[Depends(require_client)], tags=["catalog"], response_model=api_models.CatalogFoldersResponse)
    async def catalog_folders(
        source_id: str = Query(default=container.configuration.source_id),
        project_only: bool = Query(default=False),
        query: str = Query(default=""),
    ) -> dict[str, Any]:
        return {
            "folders": container.catalog.list_folders(
                source_id=source_id, project_only=project_only, query=query
            )
        }

    @app.get("/v2/catalog/folders/{relative_path:path}", dependencies=[Depends(require_client)], tags=["catalog"], response_model=api_models.CatalogFolderResponse)
    async def catalog_folder(
        relative_path: str,
        source_id: str = Query(default=container.configuration.source_id),
    ) -> dict[str, Any]:
        return container.catalog.folder_details(
            source_id=source_id, relative_path=relative_path
        )

    @app.get("/v2/catalog/project-roots", dependencies=[Depends(require_client)], tags=["catalog"], response_model=api_models.CatalogProjectRootsResponse)
    async def catalog_project_roots(
        source_id: str = Query(default=container.configuration.source_id),
    ) -> dict[str, Any]:
        return {
            "project_roots": container.catalog.list_project_roots(source_id=source_id)
        }

    @app.get("/v2/catalog/project-roots/{project_root_id}", dependencies=[Depends(require_client)], tags=["catalog"], response_model=api_models.CatalogProjectRootResponse)
    async def catalog_project_root(
        project_root_id: int,
        source_id: str = Query(default=container.configuration.source_id),
    ) -> dict[str, Any]:
        return {
            "project_root": container.catalog.project_root(
                source_id=source_id, project_root_id=project_root_id
            )
        }

    @app.get("/v2/customers", dependencies=[Depends(require_client)], tags=["customers"], response_model=api_models.CustomersResponse)
    async def list_customers(
        limit: int = Query(default=500, ge=1, le=2_000),
        offset: int = Query(default=0, ge=0),
    ) -> dict[str, Any]:
        values = container.customers.list_customers(limit=limit, offset=offset)
        return {"customers": values, "limit": limit, "offset": offset}

    @app.get("/v2/customers/{customer_id}", dependencies=[Depends(require_client)], tags=["customers"], response_model=api_models.CustomerResponse)
    async def get_customer(customer_id: int) -> dict[str, Any]:
        return {"customer": container.customers.get_customer(customer_id)}

    @app.post(
        "/v2/customers",
        dependencies=[Depends(require_client)],
        tags=["customers"],
        response_model=api_models.CustomerResponse,
        responses={409: {"model": api_models.CustomerConflictResponse}},
    )
    async def create_customer(
        payload: (
            api_models.CustomerCreateRequest
            | api_models.MutationEnvelopeRequest
            | api_models.CustomerModel
        ),
        idempotency_header: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        values = payload.model_dump(exclude_none=True)
        if isinstance(payload, api_models.MutationEnvelopeRequest):
            result = _dispatch_envelope(container, MutationEnvelope.from_dict(values))
        else:
            customer = (
                payload.customer.model_dump(exclude_none=True)
                if isinstance(payload, api_models.CustomerCreateRequest)
                else values
            )
            body_key = (
                payload.idempotency_key
                if isinstance(payload, api_models.CustomerCreateRequest)
                else None
            )
            key = _idempotency_key(idempotency_header or body_key)
            result = container.customers.create_customer(customer, idempotency_key=key)
        return _mutation_response(result)

    @app.put(
        "/v2/customers/{customer_id}",
        dependencies=[Depends(require_client)],
        tags=["customers"],
        response_model=api_models.CustomerResponse,
        responses={409: {"model": api_models.CustomerConflictResponse}},
    )
    async def update_customer(
        customer_id: int,
        payload: (
            api_models.CustomerUpdateRequest
            | api_models.MutationEnvelopeRequest
            | api_models.CustomerModel
        ),
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_header: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        if isinstance(payload, api_models.MutationEnvelopeRequest):
            return _mutation_response(
                _dispatch_envelope(
                    container, MutationEnvelope.from_dict(payload.model_dump(exclude_none=True))
                )
            )
        if isinstance(payload, api_models.CustomerUpdateRequest):
            customer = payload.customer.model_dump(exclude_none=True)
            body_revision = payload.expected_revision
            body_key = payload.idempotency_key
        else:
            customer = payload.model_dump(exclude_none=True)
            body_revision = None
            body_key = None
        expected = _expected_revision(body_revision, if_match)
        key = _idempotency_key(idempotency_header or body_key)
        result = container.customers.update_customer(
            customer_id,
            customer,
            expected_revision=expected,
            idempotency_key=key,
        )
        return _mutation_response(result)

    @app.delete(
        "/v2/customers/{customer_id}",
        dependencies=[Depends(require_client)],
        tags=["customers"],
        response_model=api_models.DeleteCustomerResponse,
        responses={409: {"model": api_models.CustomerConflictResponse}},
    )
    async def delete_customer(
        customer_id: int,
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_header: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        result = container.customers.delete_customer(
            customer_id,
            expected_revision=_expected_revision(None, if_match),
            idempotency_key=_idempotency_key(idempotency_header),
        )
        return _mutation_response(result)

    @app.post(
        "/v2/customer-mutations",
        dependencies=[Depends(require_client)],
        tags=["customers"],
        response_model=(
            api_models.CustomerResponse
            | api_models.JournalMutationResponse
            | api_models.DeleteCustomerResponse
        ),
        responses={409: {"model": api_models.CustomerConflictResponse}},
    )
    async def customer_mutation(
        payload: api_models.MutationEnvelopeRequest,
    ) -> JSONResponse:
        return _mutation_response(
            _dispatch_envelope(
                container, MutationEnvelope.from_dict(payload.model_dump(exclude_none=True))
            )
        )

    @app.get("/v2/customers/{customer_id}/journal", dependencies=[Depends(require_client)], tags=["customers"], response_model=api_models.JournalResponse)
    async def list_journal(customer_id: int) -> dict[str, Any]:
        customer = container.customers.get_customer(customer_id)
        return {
            "entries": customer.get("journal_entries", []),
            "revision": customer["revision"],
        }

    @app.post(
        "/v2/customers/{customer_id}/journal",
        dependencies=[Depends(require_client)],
        tags=["customers"],
        response_model=api_models.JournalMutationResponse,
        responses={409: {"model": api_models.CustomerConflictResponse}},
    )
    async def add_journal(
        customer_id: int,
        payload: api_models.JournalMutationRequest | api_models.MutationEnvelopeRequest,
        idempotency_header: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        if isinstance(payload, api_models.MutationEnvelopeRequest):
            return _mutation_response(
                _dispatch_envelope(
                    container, MutationEnvelope.from_dict(payload.model_dump(exclude_none=True))
                )
            )
        result = container.customers.add_journal_entry(
            customer_id,
            payload.entry.model_dump(exclude_none=True),
            expected_revision=payload.expected_revision,
            idempotency_key=_idempotency_key(
                idempotency_header or payload.idempotency_key
            ),
        )
        return _mutation_response(result)

    @app.put(
        "/v2/customers/{customer_id}/journal/{entry_id}",
        dependencies=[Depends(require_client)],
        tags=["customers"],
        response_model=api_models.JournalMutationResponse,
        responses={409: {"model": api_models.CustomerConflictResponse}},
    )
    async def update_journal(
        customer_id: int,
        entry_id: int,
        payload: api_models.JournalMutationRequest | api_models.MutationEnvelopeRequest,
        idempotency_header: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        if isinstance(payload, api_models.MutationEnvelopeRequest):
            return _mutation_response(
                _dispatch_envelope(
                    container, MutationEnvelope.from_dict(payload.model_dump(exclude_none=True))
                )
            )
        result = container.customers.update_journal_entry(
            customer_id,
            entry_id,
            payload.entry.model_dump(exclude_none=True),
            expected_revision=payload.expected_revision,
            idempotency_key=_idempotency_key(
                idempotency_header or payload.idempotency_key
            ),
        )
        return _mutation_response(result)

    @app.delete(
        "/v2/customers/{customer_id}/journal/{entry_id}",
        dependencies=[Depends(require_client)],
        tags=["customers"],
        response_model=api_models.JournalMutationResponse,
        responses={409: {"model": api_models.CustomerConflictResponse}},
    )
    async def delete_journal(
        customer_id: int,
        entry_id: int,
        if_match: str | None = Header(default=None, alias="If-Match"),
        idempotency_header: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        result = container.customers.delete_journal_entry(
            customer_id,
            entry_id,
            expected_revision=_expected_revision(None, if_match),
            idempotency_key=_idempotency_key(idempotency_header),
        )
        return _mutation_response(result)

    @app.get("/v2/customers/{customer_id}/suggestions", dependencies=[Depends(require_client)], tags=["recognition"], response_model=api_models.CustomerSuggestionsResponse)
    async def customer_suggestions(
        customer_id: int,
        suggestion_status: str | None = Query(default=None, alias="status"),
        limit: int = Query(default=500, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
        include_groups: bool = Query(default=False),
    ) -> dict[str, Any]:
        return container.suggestions.page(
            customer_id, status=suggestion_status, limit=limit, offset=offset,
            include_groups=include_groups,
        )

    @app.post(
        "/v2/customers/{customer_id}/suggestions/{suggestion_id}/decision",
        dependencies=[Depends(require_client)],
        tags=["recognition"],
        response_model=api_models.SuggestionDecisionResponse,
        responses={409: {"model": api_models.CustomerConflictResponse}},
    )
    async def decide_customer_suggestion(
        customer_id: int,
        suggestion_id: int,
        payload: api_models.SuggestionDecisionRequest,
        idempotency_header: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        result = container.suggestions.decide(
            customer_id,
            suggestion_id,
            action=payload.action,
            expected_revision=payload.expected_revision,
            idempotency_key=idempotency_header or payload.idempotency_key,
            reason=payload.reason,
        )
        return _mutation_response(result)

    @app.get("/v2/customers/{customer_id}/recognition-status", dependencies=[Depends(require_client)], tags=["recognition"], response_model=api_models.CustomerRecognitionStatusResponse)
    async def customer_recognition_status(customer_id: int) -> dict[str, Any]:
        value = container.suggestions.recognition_status(customer_id)
        value["job"] = container.recognition_jobs.latest(customer_id)
        if value["job"] and value["job"]["state"] in {"queued", "running"}:
            value["state"] = "running"
            value["summary"] = "Die Kundendaten werden geprüft."
        elif (
            value["job"]
            and value["job"]["state"] in {"error", "cancelled"}
            and _job_finished_after_run(value["job"], value.get("last_run_at", ""))
        ):
            value["state"] = "partial" if value["job"]["state"] == "cancelled" else "error"
            value["summary"] = "Die letzte Prüfung wurde abgebrochen." if value["state"] == "partial" else "Die letzte Prüfung ist fehlgeschlagen."
        return value

    @app.post("/v2/customers/{customer_id}/recognition", dependencies=[Depends(require_client)], tags=["recognition"], response_model=api_models.CustomerRecognitionJobResponse, status_code=202)
    async def start_customer_recognition(customer_id: int, payload: api_models.CustomerRecognitionRequest) -> dict[str, Any]:
        return {"job": container.recognition_jobs.request(customer_id, mode=payload.mode)}

    @app.delete("/v2/customers/{customer_id}/recognition/{job_id}", dependencies=[Depends(require_client)], tags=["recognition"], response_model=api_models.CustomerRecognitionJobResponse)
    async def cancel_customer_recognition(customer_id: int, job_id: str) -> dict[str, Any]:
        return {"job": container.recognition_jobs.cancel(customer_id, job_id)}

    @app.get("/v2/admin/recognition/blocklist", dependencies=[Depends(require_client)], tags=["admin", "recognition"], response_model=api_models.RecognitionBlocklistResponse)
    def recognition_blocklist() -> dict[str, Any]:
        return {"entries": container.recognition_blocklist.list()}

    @app.post("/v2/admin/recognition/blocklist", dependencies=[Depends(require_client)], tags=["admin", "recognition"], response_model=api_models.RecognitionBlocklistMutationResponse)
    def add_recognition_blocklist(payload: api_models.RecognitionBlocklistRequest) -> dict[str, Any]:
        return {"entry": container.recognition_blocklist.add(payload.kind, payload.value, reason=payload.reason)}

    @app.delete("/v2/admin/recognition/blocklist/{entry_id}", dependencies=[Depends(require_client)], tags=["admin", "recognition"])
    def delete_recognition_blocklist(entry_id: int) -> dict[str, bool]:
        return {"deleted": container.recognition_blocklist.delete(entry_id)}

    @app.get("/v2/admin/recognition/rebuild", dependencies=[Depends(require_client)], tags=["admin", "recognition"], response_model=api_models.RecognitionRebuildResponse)
    async def recognition_rebuild_status() -> dict[str, Any]:
        return {"job": container.recognition_jobs.latest(None)}

    @app.post("/v2/admin/recognition/rebuild", dependencies=[Depends(require_client)], tags=["admin", "recognition"], response_model=api_models.RecognitionRebuildResponse, status_code=202)
    async def start_recognition_rebuild() -> dict[str, Any]:
        return {"job": container.recognition_jobs.request(None, mode="rebuild")}

    @app.delete("/v2/admin/recognition/rebuild/{job_id}", dependencies=[Depends(require_client)], tags=["admin", "recognition"], response_model=api_models.RecognitionRebuildResponse)
    async def cancel_recognition_rebuild(job_id: str) -> dict[str, Any]:
        return {"job": container.recognition_jobs.cancel(None, job_id)}

    @app.get("/v2/recognition/cases", dependencies=[Depends(require_client)], tags=["recognition"], response_model=api_models.RecognitionCasesResponse)
    async def recognition_cases(
        recognition_status: str | None = Query(default=None, alias="status"),
    ) -> dict[str, Any]:
        return {"cases": container.recognition.list_cases(status=recognition_status)}

    @app.get("/v2/recognition/runs", dependencies=[Depends(require_client)], tags=["recognition"], response_model=api_models.RecognitionRunsResponse)
    async def recognition_runs(
        limit: int = Query(default=50, ge=1, le=500),
    ) -> dict[str, Any]:
        return {"runs": container.recognition.list_runs(limit=limit)}

    @app.post("/v2/admin/recognition/runs", dependencies=[Depends(require_client)], tags=["admin", "recognition"], response_model=api_models.RecognitionRunResponse)
    def run_recognition() -> dict[str, Any]:
        with container.coordinator.operation_lock:
            return {"summary": container.recognition.run_now(
                minimum_year=container.settings.get().minimum_customer_year
            )}

    @app.post(
        "/v2/admin/recognition/cases/{signature}/decision",
        dependencies=[Depends(require_client)],
        tags=["admin", "recognition"],
        response_model=api_models.RecognitionDecisionResponse,
        responses={409: {"model": api_models.CustomerConflictResponse}},
    )
    async def decide_recognition_case(
        signature: str,
        payload: api_models.RecognitionDecisionRequest,
        idempotency_header: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        result = container.recognition.decide(
            signature,
            action=payload.action,
            customer_id=payload.customer_id,
            expected_revision=payload.expected_revision,
            idempotency_key=idempotency_header or payload.idempotency_key,
        )
        return _mutation_response(result)

    install_v1_routes(app, container, require_client)
    return app


def _dispatch_envelope(container: ServerContainer, envelope: MutationEnvelope):
    payload = envelope.payload.to_dict() if envelope.payload is not None else {}
    key = envelope.idempotency_key.value
    if envelope.target is MutationTarget.CUSTOMER:
        if envelope.operation is MutationOperation.CREATE:
            return container.customers.create_customer(payload, idempotency_key=key)
        if envelope.operation is MutationOperation.UPDATE:
            return container.customers.update_customer(
                int(envelope.target_id), payload,
                expected_revision=int(envelope.expected_revision), idempotency_key=key,
            )
        return container.customers.delete_customer(
            int(envelope.target_id), expected_revision=int(envelope.expected_revision), idempotency_key=key,
        )
    if envelope.operation is MutationOperation.CREATE:
        if envelope.expected_revision is None:
            raise ValueError(
                "expected_revision ist für einen neuen Journaleintrag erforderlich."
            )
        return container.customers.add_journal_entry(
            int(envelope.customer_id), payload,
            expected_revision=int(envelope.expected_revision or 0), idempotency_key=key,
        )
    if envelope.operation is MutationOperation.UPDATE:
        return container.customers.update_journal_entry(
            int(envelope.customer_id), int(envelope.target_id), payload,
            expected_revision=int(envelope.expected_revision), idempotency_key=key,
        )
    return container.customers.delete_journal_entry(
        int(envelope.customer_id), int(envelope.target_id),
        expected_revision=int(envelope.expected_revision), idempotency_key=key,
    )
