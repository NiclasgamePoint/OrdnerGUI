"""Small authenticated HTTP API for index generations and customer writes."""

from __future__ import annotations

from dataclasses import asdict
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from collections.abc import Callable
from urllib.parse import unquote, urlparse

from app.core.customer_models import Contact, Customer
from app.core.config import (
    IndexOptions,
    load_customer_recognition_options,
    load_index_options,
    save_index_options,
)
from app.core.customer_recognition_models import RecognitionCandidate
from app.core.customer_repository import CustomerConflictError, CustomerRepository
from app.core.index_layout import IndexLayout
from app.services.customer_recognition import CustomerRecognitionService
from app.services.index_distribution import IndexGenerationPublisher


_CUSTOMER_WRITE_LOCK = threading.RLock()


class IndexApiServer:
    """Own the HTTP lifecycle without coupling it to the indexing loop."""

    def __init__(
        self,
        data_path: Path,
        host: str = "0.0.0.0",
        port: int = 8765,
        token: str = "",
        status_provider: Callable[[], dict[str, object]] | None = None,
        action_handler: Callable[[str], dict[str, object]] | None = None,
    ) -> None:
        self.data_path = data_path.resolve()
        self.host = host
        self.port = port
        self.token = token
        self._handler = partial(
            _IndexApiHandler,
            data_path=self.data_path,
            token=self.token,
            status_provider=status_provider,
            action_handler=action_handler,
        )
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._server = ThreadingHTTPServer((self.host, self.port), self._handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="papagui-index-api",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
        self._server = None


class _IndexApiHandler(BaseHTTPRequestHandler):
    server_version = "PapaGUIIndex/1"

    def __init__(
        self,
        *args,
        data_path: Path,
        token: str,
        status_provider=None,
        action_handler=None,
        **kwargs,
    ):
        self.data_path = data_path
        self.token = token
        self.status_provider = status_provider
        self.action_handler = action_handler
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._authorized():
            return
        path = urlparse(self.path).path
        if path == "/health":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if path == "/v1/server/status":
            if self.status_provider is None:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Status nicht verfügbar.")
            else:
                self._json(HTTPStatus.OK, self.status_provider())
            return
        if path == "/v1/server/settings":
            settings = asdict(load_index_options())
            if self.status_provider is not None:
                server = dict(self.status_provider().get("server") or {})
                settings["interval_seconds"] = server.get("interval_seconds")
            self._json(HTTPStatus.OK, {"settings": settings})
            return
        if path == "/v1/index/current":
            self._serve_file(self.data_path / "publications" / "current.json", "application/json")
            return
        archive_prefix = "/v1/index/generations/"
        if path.startswith(archive_prefix):
            name = Path(unquote(path[len(archive_prefix):])).name
            if not name.endswith(".zip"):
                self._error(HTTPStatus.NOT_FOUND, "Generation nicht gefunden.")
                return
            self._serve_file(
                self.data_path / "publications" / "generations" / name,
                "application/zip",
            )
            return
        customer_id = self._customer_id(path)
        if customer_id is not None:
            with CustomerRepository(self.data_path / "customers.db") as repository:
                customer = repository.get(customer_id)
            if customer is None:
                self._error(HTTPStatus.NOT_FOUND, "Kunde nicht gefunden.")
            else:
                self._json(HTTPStatus.OK, {"customer": asdict(customer)})
            return
        self._error(HTTPStatus.NOT_FOUND, "Unbekannter API-Pfad.")

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        path = urlparse(self.path).path
        if path == "/v1/server/actions":
            payload = self._read_json()
            if payload is None:
                return
            if self.action_handler is None:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "Steuerung nicht verfügbar.")
                return
            try:
                result = self.action_handler(str(payload.get("action", "")))
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, str(exc))
                return
            self._json(HTTPStatus.ACCEPTED, result)
            return
        if path == "/v1/customer-actions":
            payload = self._read_json()
            if payload is not None:
                self._customer_action(payload)
            return
        journal = self._journal_target(path)
        if journal is not None and journal[1] is None:
            payload = self._read_json()
            if payload is not None:
                self._journal_command("add", journal[0], None, payload)
            return
        if path != "/v1/customers":
            self._error(HTTPStatus.NOT_FOUND, "Unbekannter API-Pfad.")
            return
        payload = self._read_json()
        if payload is None:
            return
        customer = _customer_from_json(payload.get("customer", {}))
        customer.id = None
        with _CUSTOMER_WRITE_LOCK:
            with CustomerRepository(self.data_path / "customers.db") as repository:
                saved = repository.save(customer)
            self._publish()
        self._json(HTTPStatus.CREATED, {"customer": asdict(saved)})

    def _customer_action(self, payload: dict[str, object]) -> None:
        action = str(payload.get("action", ""))
        with _CUSTOMER_WRITE_LOCK:
            with CustomerRepository(self.data_path / "customers.db") as repository:
                if action == "clear_all_customer_data":
                    result: object = repository.clear_all_customer_data()
                elif action == "set_blacklist_suggestion_status":
                    result = repository.set_blacklist_suggestion_status(
                        int(payload["suggestion_id"]), str(payload["status"])
                    )
                elif action == "resolve_data_suggestion":
                    result = repository.resolve_data_suggestion(
                        int(payload["suggestion_id"]), bool(payload["accepted"])
                    )
                elif action == "resolve_recognition_case":
                    candidate = RecognitionCandidate.from_dict(
                        dict(payload.get("candidate") or {})
                    )
                    result = CustomerRecognitionService(
                        IndexLayout(self.data_path / "index").catalog_path,
                        self.data_path / "customers.db",
                        load_customer_recognition_options(),
                    ).resolve_case(
                        candidate,
                        str(payload["resolution"]),
                        int(payload["customer_id"])
                        if payload.get("customer_id") is not None
                        else None,
                    )
                else:
                    self._error(HTTPStatus.BAD_REQUEST, "Unbekannte Kundenaktion.")
                    return
            self._publish()
        encoded = asdict(result) if hasattr(result, "__dataclass_fields__") else result
        self._json(HTTPStatus.OK, {"result": encoded})

    def do_PUT(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        path = urlparse(self.path).path
        if path == "/v1/server/settings":
            payload = self._read_json()
            if payload is None:
                return
            values = payload.get("settings")
            if not isinstance(values, dict):
                self._error(HTTPStatus.BAD_REQUEST, "Indexeinstellungen fehlen.")
                return
            if "interval_seconds" in values:
                try:
                    interval = float(values["interval_seconds"])
                except (TypeError, ValueError):
                    self._error(HTTPStatus.BAD_REQUEST, "Ungültiges Indexintervall.")
                    return
                if not 900 <= interval <= 172_800:
                    self._error(
                        HTTPStatus.BAD_REQUEST,
                        "Das Indexintervall muss zwischen 15 Minuten und "
                        "48 Stunden liegen.",
                    )
                    return
            defaults = load_index_options()
            allowed = IndexOptions.__dataclass_fields__
            options = IndexOptions(
                **{
                    key: values.get(key, getattr(defaults, key))
                    for key in allowed
                }
            )
            save_index_options(options)
            if "interval_seconds" in values and self.action_handler is not None:
                self.action_handler(f"interval:{float(values['interval_seconds'])}")
            encoded = asdict(options)
            if "interval_seconds" in values:
                encoded["interval_seconds"] = values["interval_seconds"]
            self._json(HTTPStatus.OK, {"settings": encoded})
            return
        journal = self._journal_target(path)
        if journal is not None and journal[1] is not None:
            payload = self._read_json()
            if payload is not None:
                self._journal_command("update", journal[0], journal[1], payload)
            return
        customer_id = self._customer_id(path)
        if customer_id is None:
            self._error(HTTPStatus.NOT_FOUND, "Unbekannter API-Pfad.")
            return
        payload = self._read_json()
        if payload is None:
            return
        customer = _customer_from_json(payload.get("customer", {}))
        customer.id = customer_id
        try:
            with _CUSTOMER_WRITE_LOCK:
                with CustomerRepository(self.data_path / "customers.db") as repository:
                    saved = repository.save(
                        customer,
                        expected_revision=int(payload.get("expected_revision", -1)),
                    )
                self._publish()
        except CustomerConflictError as exc:
            self._json(
                HTTPStatus.CONFLICT,
                {
                    "error": str(exc),
                    "current": asdict(exc.current) if exc.current is not None else None,
                },
            )
            return
        self._json(HTTPStatus.OK, {"customer": asdict(saved)})

    def do_DELETE(self) -> None:  # noqa: N802
        if not self._authorized():
            return
        path = urlparse(self.path).path
        journal = self._journal_target(path)
        if journal is not None and journal[1] is not None:
            revision = self.headers.get("If-Match", "").strip().strip('"')
            if not revision.isdigit():
                self._error(HTTPStatus.PRECONDITION_REQUIRED, "If-Match-Revision fehlt.")
                return
            self._journal_command(
                "delete",
                journal[0],
                journal[1],
                {"expected_revision": int(revision)},
            )
            return
        customer_id = self._customer_id(path)
        if customer_id is None:
            self._error(HTTPStatus.NOT_FOUND, "Unbekannter API-Pfad.")
            return
        revision = self.headers.get("If-Match", "").strip().strip('"')
        if not revision.isdigit():
            self._error(HTTPStatus.PRECONDITION_REQUIRED, "If-Match-Revision fehlt.")
            return
        try:
            with _CUSTOMER_WRITE_LOCK:
                with CustomerRepository(self.data_path / "customers.db") as repository:
                    repository.delete(customer_id, expected_revision=int(revision))
                self._publish()
        except CustomerConflictError as exc:
            self._json(
                HTTPStatus.CONFLICT,
                {
                    "error": str(exc),
                    "current": asdict(exc.current) if exc.current is not None else None,
                },
            )
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def _authorized(self) -> bool:
        if not self.token:
            return True
        if self.headers.get("Authorization") == f"Bearer {self.token}":
            return True
        self._error(HTTPStatus.UNAUTHORIZED, "Authentifizierung erforderlich.")
        return False

    @staticmethod
    def _customer_id(path: str) -> int | None:
        prefix = "/v1/customers/"
        value = path[len(prefix):] if path.startswith(prefix) else ""
        return int(value) if value.isdigit() else None

    @staticmethod
    def _journal_target(path: str) -> tuple[int, int | None] | None:
        parts = path.strip("/").split("/")
        if len(parts) not in {4, 5} or parts[:2] != ["v1", "customers"]:
            return None
        if not parts[2].isdigit() or parts[3] != "journal":
            return None
        if len(parts) == 5 and not parts[4].isdigit():
            return None
        return int(parts[2]), int(parts[4]) if len(parts) == 5 else None

    def _journal_command(
        self,
        operation: str,
        customer_id: int,
        entry_id: int | None,
        payload: dict[str, object],
    ) -> None:
        expected = int(payload.get("expected_revision", -1))
        try:
            with _CUSTOMER_WRITE_LOCK:
                with CustomerRepository(self.data_path / "customers.db") as repository:
                    current = repository.get(customer_id)
                    if current is None or current.revision != expected:
                        raise CustomerConflictError(current)
                    if operation == "add":
                        result = repository.add_journal_entry(
                            customer_id,
                            str(payload.get("body", "")),
                            str(payload.get("title", "")),
                        )
                    elif operation == "update":
                        result = repository.update_journal_entry(
                            customer_id,
                            int(entry_id),
                            str(payload.get("body", "")),
                            str(payload.get("title", "")),
                        )
                    else:
                        result = repository.delete_journal_entry(customer_id, int(entry_id))
                    repository.connection.execute(
                        "UPDATE customers SET revision=revision+1 WHERE id=?", (customer_id,)
                    )
                    repository.connection.commit()
                    revision = repository.get(customer_id).revision
                self._publish()
        except CustomerConflictError as exc:
            self._json(
                HTTPStatus.CONFLICT,
                {
                    "error": str(exc),
                    "current": asdict(exc.current) if exc.current is not None else None,
                },
            )
            return
        encoded_result = asdict(result) if hasattr(result, "__dataclass_fields__") else result
        self._json(HTTPStatus.OK, {"result": encoded_result, "revision": revision})

    def _publish(self) -> None:
        """Expose every accepted customer mutation as a complete generation."""
        IndexGenerationPublisher(self.data_path).publish()

    def _read_json(self) -> dict[str, object] | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, json.JSONDecodeError):
            self._error(HTTPStatus.BAD_REQUEST, "Ungültiges JSON.")
            return None

    def _serve_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self._error(HTTPStatus.NOT_FOUND, "Datei nicht gefunden.")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.end_headers()
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                self.wfile.write(chunk)

    def _json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json(status, {"error": message})

    def log_message(self, _format: str, *_args: object) -> None:
        return


def _customer_from_json(payload: object) -> Customer:
    if not isinstance(payload, dict):
        raise ValueError("Kundendaten fehlen.")
    values = dict(payload)
    values["contacts"] = [Contact(**item) for item in values.get("contacts", [])]
    allowed = Customer.__dataclass_fields__
    return Customer(**{key: value for key, value in values.items() if key in allowed})
