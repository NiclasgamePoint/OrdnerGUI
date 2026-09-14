"""Standard-library HTTP adapters for the PapaGUI v2 API."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any, Iterable, Mapping
import urllib.error
import urllib.parse
import urllib.request
import uuid

from papagui_contracts.customers import (
    Customer,
    IdempotencyKey,
    MutationEnvelope,
    MutationOperation,
    MutationTarget,
)
from papagui_contracts.generations import (
    GenerationComponentKind,
    GenerationComponentManifest,
    GenerationManifest,
)
from papagui_contracts.recognition import (
    CustomerSuggestion,
    RecognitionCase,
    RecognitionDecision,
    RecognitionRunSummary,
)

from papagui_client.application.models import (
    CustomerMutationKind,
    GatewayMutationResult,
    PendingCustomerMutation,
)
from papagui_client.application.errors import (
    CustomerGatewayConflict,
    CustomerGatewayIdempotencyConflict,
    CustomerGatewayUnavailable,
    GenerationNotReady,
)


class ApiUnavailableError(CustomerGatewayUnavailable):
    pass


class ApiRejectedError(RuntimeError):
    def __init__(self, status: int, message: str, payload: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.payload = dict(payload or {})


class ApiConflictError(ApiRejectedError, CustomerGatewayConflict):
    def __init__(self, current: Customer | None, payload: Mapping[str, Any] | None = None):
        ApiRejectedError.__init__(self, 409, "customer revision conflict", payload)
        self.current = current


class ApiIdempotencyConflictError(ApiRejectedError, CustomerGatewayIdempotencyConflict):
    def __init__(self, payload: Mapping[str, Any] | None = None):
        ApiRejectedError.__init__(self, 409, "idempotency key conflict", payload)


class _HttpTransport:
    def __init__(self, base_url: str, token: str = "", timeout_seconds: float = 10):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout_seconds = timeout_seconds

    def json(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> Mapping[str, Any]:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(self.url(path), data=data, method=method)
        request.add_header("Accept", "application/json")
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        for name, value in (headers or {}).items():
            request.add_header(name, value)
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds if timeout_seconds is None else timeout_seconds,
            ) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                error = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, ValueError):
                error = {}
            if not isinstance(error, dict):
                error = {}
            message = str(error.get("detail") or error.get("error") or exc.reason)
            raise ApiRejectedError(exc.code, message, error) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise ApiUnavailableError(str(exc)) from exc
        if not body:
            return {}
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ApiUnavailableError("server returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise ApiUnavailableError("server returned a non-object JSON response")
        return value

    def download(self, path: str, destination: Path) -> None:
        request = urllib.request.Request(self.url(path), method="GET")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                with destination.open("wb") as target:
                    shutil.copyfileobj(response, target)
        except urllib.error.HTTPError as exc:
            raise ApiRejectedError(exc.code, str(exc.reason)) from exc
        except (OSError, urllib.error.URLError) as exc:
            raise ApiUnavailableError(str(exc)) from exc

    def url(self, path: str) -> str:
        parsed = urllib.parse.urlparse(path)
        return path if parsed.scheme in {"http", "https"} else f"{self.base_url}/{path.lstrip('/')}"


class HttpGenerationGateway:
    def __init__(self, server_url: str, token: str = "", timeout_seconds: float = 15):
        self._transport = _HttpTransport(server_url, token, timeout_seconds)
        self._api_version = 2

    def current_manifest(self) -> GenerationManifest:
        try:
            payload = self._transport.json("GET", "/v2/generations/current")
            self._api_version = 2
        except ApiRejectedError as exc:
            if exc.status != 404:
                raise
            if self._generation_pending(exc):
                raise GenerationNotReady from exc
            try:
                payload = self._transport.json("GET", "/v1/index/current")
            except ApiRejectedError as legacy_exc:
                if self._generation_pending(legacy_exc):
                    raise GenerationNotReady from legacy_exc
                raise
            self._api_version = 1
        return GenerationManifest.from_dict(payload)

    @staticmethod
    def _generation_pending(error: ApiRejectedError) -> bool:
        detail = error.payload.get("error")
        return (
            error.status == 404
            and isinstance(detail, Mapping)
            and detail.get("code") == "not_found"
        )

    def download_component(
        self,
        kind: GenerationComponentKind,
        component: GenerationComponentManifest,
        destination: Path,
    ) -> None:
        archive = component.archive
        if self._api_version == 1:
            archive = f"/v1/index/generations/{urllib.parse.quote(Path(archive).name)}"
            self._transport.download(archive, destination)
            return
        if not urllib.parse.urlparse(archive).scheme and "/" not in archive:
            archive = f"/v2/generations/{kind.value}/{component.generation}/archive"
        self._transport.download(archive, destination)


class HttpCustomerGateway:
    def __init__(self, server_url: str, token: str = "", timeout_seconds: float = 10):
        self._transport = _HttpTransport(server_url, token, timeout_seconds)

    def list_customers(self) -> list[Customer]:
        payload = self._transport.json("GET", "/v2/customers")
        items = payload.get("customers", payload.get("items", ()))
        return [Customer.from_dict(item) for item in items if isinstance(item, dict)]

    def get_customer(self, customer_id: int) -> Customer | None:
        try:
            payload = self._transport.json("GET", f"/v2/customers/{customer_id}")
        except ApiRejectedError as exc:
            if exc.status == 404:
                return None
            raise
        value = payload.get("customer", payload)
        return Customer.from_dict(value) if isinstance(value, dict) else None

    def mutate(self, mutation: PendingCustomerMutation) -> GatewayMutationResult:
        headers = {
            "Idempotency-Key": mutation.idempotency_key,
            "If-Match": str(mutation.expected_revision),
        }
        if mutation.operation is CustomerMutationKind.CREATE:
            method, path = "POST", "/v2/customers"
            customer = Customer.from_dict(mutation.payload)
            envelope = MutationEnvelope(
                operation=MutationOperation.CREATE,
                target=MutationTarget.CUSTOMER,
                idempotency_key=IdempotencyKey(mutation.idempotency_key),
                payload=customer,
            )
        elif mutation.operation is CustomerMutationKind.UPDATE:
            customer = Customer.from_dict(mutation.payload)
            customer_id = customer.id
            method, path = "PUT", f"/v2/customers/{customer_id}"
            envelope = MutationEnvelope(
                operation=MutationOperation.UPDATE,
                target=MutationTarget.CUSTOMER,
                idempotency_key=IdempotencyKey(mutation.idempotency_key),
                expected_revision=mutation.expected_revision,
                target_id=customer_id,
                payload=customer,
            )
        else:
            customer_id = int(mutation.aggregate_key)
            method, path = "DELETE", f"/v2/customers/{customer_id}"
            envelope = MutationEnvelope(
                operation=MutationOperation.DELETE,
                target=MutationTarget.CUSTOMER,
                idempotency_key=IdempotencyKey(mutation.idempotency_key),
                expected_revision=mutation.expected_revision,
                target_id=customer_id,
            )
        payload = envelope.to_dict()
        try:
            response = self._transport.json(method, path, payload, headers)
        except ApiRejectedError as exc:
            if exc.status != 409:
                raise
            error = exc.payload.get("error")
            code = error.get("code") if isinstance(error, Mapping) else None
            if code == "idempotency_conflict":
                raise ApiIdempotencyConflictError(exc.payload) from exc
            if code not in {None, "customer_revision_conflict"}:
                raise
            current = exc.payload.get("current")
            raise ApiConflictError(
                Customer.from_dict(current) if isinstance(current, dict) else None,
                exc.payload,
            ) from exc
        value = response.get("customer")
        return GatewayMutationResult(
            customer=Customer.from_dict(value) if isinstance(value, dict) else None,
            deleted=mutation.operation is CustomerMutationKind.DELETE,
        )


class HttpServerControlGateway:
    """Tray-facing API client for token-protected server controls."""

    def __init__(self, server_url: str, token: str = "", timeout_seconds: float = 5):
        self._transport = _HttpTransport(server_url, token, timeout_seconds)

    def status(self) -> Mapping[str, Any]:
        return self._transport.json("GET", "/v2/server/status")

    def settings(self) -> Mapping[str, Any]:
        return self._transport.json("GET", "/v2/admin/settings")

    def save_settings(self, settings: Mapping[str, Any]) -> Mapping[str, Any]:
        return self._transport.json(
            "PUT", "/v2/admin/settings", {"settings": dict(settings)}
        )

    def index_action(self, action: str) -> Mapping[str, Any]:
        routes = {
            "start": ("POST", "/v2/admin/index-runs", {"full_rebuild": False}),
            "full_rebuild": ("POST", "/v2/admin/index-runs", {"full_rebuild": True}),
            "cancel": ("POST", "/v2/admin/index-runs/current/cancel", None),
            "delete": ("DELETE", "/v2/admin/index?rebuild=true", None),
            "restart_server": ("POST", "/v2/admin/server/restart", None),
        }
        try:
            method, path, payload = routes[action]
        except KeyError as exc:
            raise ValueError(f"unknown server action: {action}") from exc
        return self._transport.json(method, path, payload)

    def recognition_cases(self, status: str = "pending") -> tuple[RecognitionCase, ...]:
        query = urllib.parse.urlencode({"status": status}) if status else ""
        response = self._transport.json(
            "GET", "/v2/recognition/cases" + (f"?{query}" if query else "")
        )
        return tuple(
            RecognitionCase.from_dict(item)
            for item in response.get("cases", ())
            if isinstance(item, Mapping)
        )

    def recognition_runs(self, limit: int = 50) -> tuple[RecognitionRunSummary, ...]:
        if not 1 <= limit <= 500:
            raise ValueError("recognition run limit must be between 1 and 500")
        response = self._transport.json("GET", f"/v2/recognition/runs?limit={limit}")
        return tuple(
            RecognitionRunSummary.from_dict(item)
            for item in response.get("runs", ())
            if isinstance(item, Mapping)
        )

    def start_recognition(self) -> RecognitionRunSummary:
        response = self._transport.json(
            "POST",
            "/v2/admin/recognition/runs",
            {},
        )
        value = response.get("summary", response)
        if not isinstance(value, Mapping):
            raise ApiUnavailableError("server returned an invalid recognition summary")
        return RecognitionRunSummary.from_dict(value)

    def recognition_blocklist(self) -> tuple[dict[str, Any], ...]:
        response = self._transport.json("GET", "/v2/admin/recognition/blocklist")
        entries = response.get("entries")
        if not isinstance(entries, list) or any(
            not isinstance(entry, Mapping) for entry in entries
        ):
            raise ApiUnavailableError("server returned an invalid recognition blocklist")
        return tuple(dict(entry) for entry in entries)

    def recognition_blocklist_state(self) -> dict[str, Any]:
        response = self._transport.json("GET", "/v2/admin/recognition/blocklist")
        entries = response.get("entries")
        publication_pending = response.get("publication_pending", False)
        if (
            not isinstance(entries, list)
            or any(not isinstance(entry, Mapping) for entry in entries)
            or not isinstance(publication_pending, bool)
        ):
            raise ApiUnavailableError("server returned an invalid recognition blocklist state")
        return {
            "entries": [dict(entry) for entry in entries],
            "publication_pending": publication_pending,
        }

    def add_recognition_blocklist_entry(
        self, kind: str, value: str, reason: str = ""
    ) -> dict[str, Any]:
        response = self._transport.json(
            "POST",
            "/v2/admin/recognition/blocklist",
            {"kind": kind, "value": value, "reason": reason},
        )
        entry = response.get("entry")
        if not isinstance(entry, Mapping):
            raise ApiUnavailableError("server returned an invalid recognition blocklist entry")
        return dict(entry)

    def delete_recognition_blocklist_entry(self, entry_id: int) -> bool:
        response = self._transport.json(
            "DELETE", f"/v2/admin/recognition/blocklist/{int(entry_id)}"
        )
        if response.get("deleted") is not True:
            raise ApiUnavailableError("server did not confirm recognition blocklist deletion")
        return True

    def apply_recognition_blocklist_changes(
        self,
        additions: Iterable[Mapping[str, str]],
        deletions: Iterable[int],
    ) -> dict[str, Any]:
        try:
            response = self._transport.json(
                "POST",
                "/v2/admin/recognition/blocklist/batch",
                {
                    "additions": [dict(entry) for entry in additions],
                    "deletions": list(deletions),
                },
                # Publishing the customer snapshot can exceed the short polling timeout.
                timeout_seconds=max(self._transport.timeout_seconds, 60),
            )
        except ApiRejectedError as exc:
            if exc.status not in {404, 405}:
                raise
            message = (
                "Für das gemeinsame Speichern der Sperrliste ist ein Serverupdate erforderlich. "
                "Bitte aktualisieren Sie den Server und versuchen Sie es erneut."
            )
            raise ApiRejectedError(
                exc.status,
                message,
                {"error": {"code": "recognition_blocklist_batch_unsupported", "message": message}},
            ) from exc
        entries = response.get("entries")
        changed = response.get("changed")
        published = response.get("published")
        if (
            not isinstance(entries, list)
            or not isinstance(changed, bool)
            or not isinstance(published, bool)
        ):
            raise ApiUnavailableError("server returned an invalid recognition blocklist batch result")
        for entry in entries:
            if (
                not isinstance(entry, Mapping)
                or type(entry.get("id")) is not int
                or entry["id"] <= 0
                or not isinstance(entry.get("kind"), str)
                or not entry["kind"].strip()
                or not isinstance(entry.get("value"), str)
                or not entry["value"].strip()
                or not isinstance(entry.get("reason", ""), str)
            ):
                raise ApiUnavailableError(
                    "server returned an invalid recognition blocklist batch entry"
                )
        return {
            "entries": [dict(entry) for entry in entries],
            "changed": changed,
            "published": published,
        }

    def recognition_rebuild(self) -> dict[str, Any] | None:
        response = self._transport.json("GET", "/v2/admin/recognition/rebuild")
        if "job" not in response:
            raise ApiUnavailableError("server returned an invalid recognition rebuild status")
        job = response["job"]
        if job is None:
            return None
        return self._recognition_rebuild_job(response)

    def start_recognition_rebuild(self) -> dict[str, Any]:
        response = self._transport.json("POST", "/v2/admin/recognition/rebuild", {})
        return self._recognition_rebuild_job(response)

    def cancel_recognition_rebuild(self, job_id: str) -> dict[str, Any]:
        response = self._transport.json(
            "DELETE",
            "/v2/admin/recognition/rebuild/"
            f"{urllib.parse.quote(str(job_id), safe='')}",
        )
        return self._recognition_rebuild_job(response)

    @staticmethod
    def _recognition_rebuild_job(response: Mapping[str, Any]) -> dict[str, Any]:
        job = response.get("job")
        if not isinstance(job, Mapping) or not job.get("id") or not job.get("state"):
            raise ApiUnavailableError("server returned an invalid recognition rebuild job")
        return dict(job)

    def decide_recognition(
        self,
        signature: str,
        action: str,
        customer_id: int | None = None,
        expected_revision: int | None = None,
        *,
        idempotency_key: str | None = None,
    ) -> tuple[RecognitionDecision, Customer | None]:
        payload: dict[str, Any] = {"action": action}
        if customer_id is not None:
            payload["customer_id"] = customer_id
        if expected_revision is not None:
            payload["expected_revision"] = expected_revision
        headers: dict[str, str] = {}
        if action in {"accept", "assign"}:
            key = idempotency_key or str(uuid.uuid4())
            payload["idempotency_key"] = key
            headers["Idempotency-Key"] = key
        try:
            response = self._transport.json(
                "POST",
                "/v2/admin/recognition/cases/"
                f"{urllib.parse.quote(signature, safe='')}/decision",
                payload,
                headers,
            )
        except ApiRejectedError as exc:
            if exc.status != 409:
                raise
            error = exc.payload.get("error")
            code = error.get("code") if isinstance(error, Mapping) else None
            if code == "idempotency_conflict":
                raise ApiIdempotencyConflictError(exc.payload) from exc
            current = exc.payload.get("current")
            raise ApiConflictError(
                Customer.from_dict(current) if isinstance(current, Mapping) else None,
                exc.payload,
            ) from exc
        value = response.get("decision", response)
        if not isinstance(value, Mapping):
            raise ApiUnavailableError("server returned an invalid recognition decision")
        customer = response.get("customer")
        return (
            RecognitionDecision.from_dict(value),
            Customer.from_dict(customer) if isinstance(customer, Mapping) else None,
        )

    def customer_suggestions(
        self, customer_id: int, status: str = "pending"
    ) -> tuple[tuple[CustomerSuggestion, ...], int]:
        query = urllib.parse.urlencode({"status": status}) if status else ""
        response = self._transport.json(
            "GET",
            f"/v2/customers/{customer_id}/suggestions" + (f"?{query}" if query else ""),
        )
        return (
            tuple(
                CustomerSuggestion.from_dict(item)
                for item in response.get("suggestions", ())
                if isinstance(item, Mapping)
            ),
            int(response.get("revision", 0)),
        )

    def decide_suggestion(
        self,
        customer_id: int,
        suggestion_id: int,
        action: str,
        expected_revision: int,
        *,
        idempotency_key: str | None = None,
    ) -> tuple[CustomerSuggestion, Customer]:
        key = idempotency_key or str(uuid.uuid4())
        try:
            response = self._transport.json(
                "POST",
                f"/v2/customers/{customer_id}/suggestions/{suggestion_id}/decision",
                {
                    "action": action,
                    "expected_revision": expected_revision,
                    "idempotency_key": key,
                },
                {
                    "Idempotency-Key": key,
                    "If-Match": str(expected_revision),
                },
            )
        except ApiRejectedError as exc:
            if exc.status != 409:
                raise
            error = exc.payload.get("error")
            code = error.get("code") if isinstance(error, Mapping) else None
            if code == "idempotency_conflict":
                raise ApiIdempotencyConflictError(exc.payload) from exc
            current = exc.payload.get("current")
            raise ApiConflictError(
                Customer.from_dict(current) if isinstance(current, Mapping) else None,
                exc.payload,
            ) from exc
        suggestion = response.get("suggestion")
        customer = response.get("customer")
        if not isinstance(suggestion, Mapping) or not isinstance(customer, Mapping):
            raise ApiUnavailableError("server returned an invalid suggestion decision")
        return CustomerSuggestion.from_dict(suggestion), Customer.from_dict(customer)
