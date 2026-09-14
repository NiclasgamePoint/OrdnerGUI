"""Real HTTP interoperability gate for the independently packaged products."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from typing import Callable, Iterator, TypeVar
import urllib.error
import urllib.request

import pytest

from papagui_contracts import Customer, CustomerJournalEntry, GenerationComponentKind
from papagui_client.adapters.filesystem_generations import FilesystemGenerationStore
from papagui_client.adapters.http_api import (
    ApiConflictError,
    HttpCustomerGateway,
    HttpGenerationGateway,
    HttpServerControlGateway,
)
from papagui_client.adapters.http_journal import HttpJournalGateway
from papagui_client.adapters.sqlite_catalog_reader import SQLiteCatalogReader
from papagui_client.application.catalog import CatalogSearchService
from papagui_client.application.models import (
    CatalogQuery,
    CustomerMutationKind,
    JournalMutationKind,
    PendingCustomerMutation,
    PendingJournalMutation,
)
from papagui_client.application.paths import SourceMapping, SourcePathResolver
from papagui_client.application.sync import SyncCoordinator


ROOT = Path(__file__).resolve().parents[2]
CLIENT_TOKEN = "wire-client-token-123"
SOURCE_ID = "wire-source"
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class RunningServer:
    base_url: str
    process: subprocess.Popen[str]
    log_path: Path


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _server_log(server: RunningServer) -> str:
    try:
        return server.log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "<server log unavailable>"


def _wait_for(
    operation: Callable[[], T],
    *,
    server: RunningServer,
    timeout: float = 60.0,
) -> T:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if server.process.poll() is not None:
            pytest.fail(
                f"papagui_server exited early with {server.process.returncode}\n"
                f"{_server_log(server)}"
            )
        try:
            return operation()
        except (OSError, RuntimeError, urllib.error.URLError) as error:
            last_error = error
            time.sleep(0.1)
    pytest.fail(f"papagui_server did not become ready: {last_error}\n{_server_log(server)}")


def _health(base_url: str) -> None:
    with urllib.request.urlopen(f"{base_url}/health", timeout=1) as response:
        if response.status != 200:
            raise RuntimeError(f"unexpected health status {response.status}")


@contextmanager
def _running_server(tmp_path: Path) -> Iterator[RunningServer]:
    source = tmp_path / "source"
    data = tmp_path / "server-data"
    config = tmp_path / "server-config"
    source.mkdir()
    project = source / "Planung" / "2026" / "Wirekunde, Berlin"
    project.mkdir(parents=True)
    (project / "wire-document.txt").write_text(
        "PapaGUI wire interoperability payload", encoding="utf-8"
    )

    port = _free_port()
    log_path = tmp_path / "papagui-server.log"
    environment = os.environ.copy()
    server_root = Path(environment.get("PAPAGUI_TEST_SERVER_ROOT", str(ROOT)))
    package_sources = (
        server_root / "packages" / "contracts" / "src",
        server_root / "packages" / "server" / "src",
    )
    python_path = [str(path) for path in package_sources]
    if existing := environment.get("PYTHONPATH"):
        python_path.append(existing)
    environment.update(
        {
            "PYTHONPATH": os.pathsep.join(python_path),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PAPAGUI_API_TOKEN": CLIENT_TOKEN,
        }
    )
    command = [
        sys.executable,
        "-m",
        "papagui_server",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--source",
        str(source),
        "--data",
        str(data),
        "--config",
        str(config),
        "--source-id",
        SOURCE_ID,
        "--interval-seconds",
        "86400",
    ]
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        server = RunningServer(f"http://127.0.0.1:{port}", process, log_path)
        try:
            _wait_for(lambda: _health(server.base_url), server=server, timeout=30)
            yield server
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


def _mutation(
    operation: CustomerMutationKind,
    customer: Customer,
    *,
    key: str,
    expected_revision: int | None = None,
) -> PendingCustomerMutation:
    return PendingCustomerMutation(
        sequence=1,
        idempotency_key=key,
        aggregate_key=str(customer.id or "local:wire-customer"),
        operation=operation,
        expected_revision=(customer.revision if expected_revision is None else expected_revision),
        payload=customer.to_dict(),
    )


def _journal_mutation(
    customer_id: int,
    entry: CustomerJournalEntry,
    *,
    key: str,
    expected_revision: int,
) -> PendingJournalMutation:
    return PendingJournalMutation(
        sequence=1,
        idempotency_key=key,
        aggregate_key="local:wire-journal",
        customer_id=customer_id,
        operation=JournalMutationKind.CREATE,
        expected_revision=expected_revision,
        payload=entry.to_dict(),
    )


def _tree_digests(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_client_and_server_interoperate_over_real_http(tmp_path: Path) -> None:
    with _running_server(tmp_path) as server:
        generation_gateway = HttpGenerationGateway(server.base_url, CLIENT_TOKEN, timeout_seconds=3)
        initial_manifest = _wait_for(generation_gateway.current_manifest, server=server)
        assert initial_manifest.index is not None
        assert initial_manifest.customers is not None

        store = FilesystemGenerationStore(tmp_path / "client-cache")
        sync_result = SyncCoordinator(generation_gateway, store).sync()
        assert sync_result.changed_components == ("index", "customers")

        index_root = store.active_component_path(GenerationComponentKind.INDEX)
        customer_root = store.active_component_path(GenerationComponentKind.CUSTOMERS)
        assert index_root is not None
        assert customer_root is not None
        catalog_database = index_root / "index" / "catalog" / "active.db"
        customer_database = customer_root / "customers.db"
        assert catalog_database.is_file()
        assert customer_database.is_file()
        immutable_before = {
            "index": _tree_digests(index_root),
            "customers": _tree_digests(customer_root),
        }

        source = tmp_path / "source"
        search = CatalogSearchService(
            SQLiteCatalogReader(catalog_database, customer_database),
            SourcePathResolver(
                [
                    SourceMapping(
                        SOURCE_ID,
                        windows=str(source),
                        macos=str(source),
                        linux=str(source),
                    )
                ]
            ),
        )
        hit = next(
            item
            for item in search.search(CatalogQuery(source_id=SOURCE_ID))
            if item.record.filename == "wire-document.txt"
        )
        assert hit.record.relative_path == ("Planung/2026/Wirekunde, Berlin/wire-document.txt")
        assert Path(hit.local_path) == source / hit.record.relative_path

        customers = HttpCustomerGateway(server.base_url, CLIENT_TOKEN, timeout_seconds=3)
        create = _mutation(
            CustomerMutationKind.CREATE,
            Customer(display_name="Wire API Kunde", city="Hamburg"),
            key="wire-create-0001",
        )
        created = customers.mutate(create).customer
        assert created is not None
        assert created.id is not None
        assert created.revision == 1

        replayed = customers.mutate(create).customer
        assert replayed == created
        assert len([item for item in customers.list_customers() if item.id == created.id]) == 1

        after_create = generation_gateway.current_manifest()
        assert after_create.index is not None
        assert after_create.customers is not None
        assert after_create.index.generation == initial_manifest.index.generation
        assert after_create.customers.generation != initial_manifest.customers.generation

        winner = replace(created, display_name="Wire API Gewinner")
        saved = customers.mutate(
            _mutation(
                CustomerMutationKind.UPDATE,
                winner,
                key="wire-update-0001",
                expected_revision=created.revision,
            )
        ).customer
        assert saved is not None
        assert saved.revision == created.revision + 1

        stale = replace(created, display_name="Wire API Parallel")
        with pytest.raises(ApiConflictError) as conflict:
            customers.mutate(
                _mutation(
                    CustomerMutationKind.UPDATE,
                    stale,
                    key="wire-update-0002",
                    expected_revision=created.revision,
                )
            )
        assert conflict.value.status == 409
        assert conflict.value.current == saved

        journals = HttpJournalGateway(server.base_url, CLIENT_TOKEN, timeout_seconds=3)
        journal_create = _journal_mutation(
            saved.id,
            CustomerJournalEntry(title="Wire-Termin", body="Per HTTP angelegt"),
            key="wire-journal-create-0001",
            expected_revision=saved.revision,
        )
        journal_created = journals.mutate(journal_create)
        assert journal_created.entry is not None
        assert journal_created.entry.id is not None
        assert journal_created.entry.customer_id == saved.id
        assert journal_created.entry.title == "Wire-Termin"
        assert journal_created.revision == saved.revision + 1

        journal_replayed = journals.mutate(journal_create)
        assert journal_replayed == journal_created
        listed_entries, listed_revision = journals.list_entries(saved.id)
        assert listed_revision == journal_created.revision
        assert [entry.id for entry in listed_entries] == [journal_created.entry.id]

        stale_journal = _journal_mutation(
            saved.id,
            CustomerJournalEntry(title="Veraltet", body="Darf nicht angelegt werden"),
            key="wire-journal-create-0002",
            expected_revision=saved.revision,
        )
        with pytest.raises(ApiConflictError) as journal_conflict:
            journals.mutate(stale_journal)
        assert journal_conflict.value.status == 409
        assert journal_conflict.value.current is not None
        assert journal_conflict.value.current.revision == journal_created.revision
        listed_after_conflict, _ = journals.list_entries(saved.id)
        assert [entry.id for entry in listed_after_conflict] == [journal_created.entry.id]

        control = HttpServerControlGateway(server.base_url, CLIENT_TOKEN, timeout_seconds=3)
        settings = control.settings()
        assert settings["settings"]["interval_seconds"] == 86400
        updated = control.save_settings({"interval_seconds": 900})
        assert updated["settings"]["interval_seconds"] == 900
        assert control.settings()["settings"]["interval_seconds"] == 900

        assert _tree_digests(index_root) == immutable_before["index"]
        assert _tree_digests(customer_root) == immutable_before["customers"]
        assert server.process.poll() is None
