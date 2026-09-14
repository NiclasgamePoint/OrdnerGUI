from __future__ import annotations

import io
import json
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
import urllib.error
from unittest.mock import Mock, patch

import pytest

from papagui_contracts import Customer
from papagui_contracts.generations import (
    GenerationComponentKind,
    GenerationComponentManifest,
)
from papagui_client.adapters.http_api import (
    ApiConflictError,
    ApiIdempotencyConflictError,
    ApiRejectedError,
    ApiUnavailableError,
    HttpCustomerGateway,
    HttpGenerationGateway,
    HttpServerControlGateway,
    _HttpTransport,
)
from papagui_client.application.models import (
    CustomerMutationKind,
    PendingCustomerMutation,
    SyncResult,
)
from papagui_client.application.sync import SyncError
from papagui_client.application.errors import GenerationNotReady
from papagui_client.composition import ClientContainer, build_client, build_tray
from papagui_client.config import ClientSettings, default_data_root
from papagui_client.entrypoints import client as client_entrypoint
from papagui_client.entrypoints import tray as tray_entrypoint
from papagui_client.gui.launcher import TrayProcessLauncher


class Response:
    def __init__(self, body: bytes):
        self.body = body
        self._offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, size: int = -1):
        if size < 0:
            result = self.body[self._offset :]
            self._offset = len(self.body)
            return result
        result = self.body[self._offset : self._offset + size]
        self._offset += len(result)
        return result


def _http_error(code: int, body: bytes = b"", reason: str = "rejected"):
    return urllib.error.HTTPError(
        "http://server/resource", code, reason, {}, io.BytesIO(body)
    )


def _customer(customer_id: int | None = 7, revision: int = 2) -> Customer:
    return Customer(id=customer_id, revision=revision, display_name="Muster")


def _mutation(
    operation: CustomerMutationKind,
    *,
    customer: Customer | None = None,
) -> PendingCustomerMutation:
    value = customer or _customer()
    return PendingCustomerMutation(
        sequence=1,
        idempotency_key="idem-key-0001",
        aggregate_key=str(value.id or "local:one"),
        operation=operation,
        expected_revision=value.revision,
        payload=value.to_dict() if operation is not CustomerMutationKind.DELETE else {},
    )


def test_http_transport_json_headers_payload_and_response_validation(monkeypatch):
    captured = []

    def open_ok(request, timeout):
        captured.append((request, timeout))
        return Response(json.dumps({"ok": True}).encode())

    monkeypatch.setattr("urllib.request.urlopen", open_ok)
    transport = _HttpTransport("http://server/", "secret", 3)
    assert transport.json("POST", "/resource", {"a": 1}, {"X-Test": "yes"}) == {"ok": True}
    request, timeout = captured[0]
    assert request.full_url == "http://server/resource"
    assert request.method == "POST"
    assert request.get_header("Authorization") == "Bearer secret"
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("X-test") == "yes"
    assert timeout == 3
    assert transport.url("https://elsewhere.test/data") == "https://elsewhere.test/data"

    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: Response(b""))
    assert transport.json("GET", "/empty") == {}
    for body in (b"not-json", b"[]", b"\xff"):
        monkeypatch.setattr("urllib.request.urlopen", lambda *_a, value=body, **_k: Response(value))
        with pytest.raises(ApiUnavailableError):
            transport.json("GET", "/invalid")


@pytest.mark.parametrize(
    ("body", "message", "payload"),
    [
        (b'{"detail":"bad detail"}', "bad detail", {"detail": "bad detail"}),
        (b'{"error":"bad error"}', "bad error", {"error": "bad error"}),
        (b"broken", "rejected", {}),
        (b"", "rejected", {}),
    ],
)
def test_http_transport_maps_http_errors(monkeypatch, body, message, payload):
    def rejected(*_args, **_kwargs):
        raise _http_error(422, body)

    monkeypatch.setattr("urllib.request.urlopen", rejected)
    with pytest.raises(ApiRejectedError) as caught:
        _HttpTransport("http://server").json("GET", "/resource")
    assert caught.value.status == 422
    assert str(caught.value) == message
    assert caught.value.payload == payload


def test_http_transport_download_and_unavailable_errors(monkeypatch, tmp_path):
    destination = tmp_path / "generation.zip"
    monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: Response(b"archive"))
    _HttpTransport("http://server", "token").download("/archive", destination)
    assert destination.read_bytes() == b"archive"

    def unavailable(*_args, **_kwargs):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("urllib.request.urlopen", unavailable)
    with pytest.raises(ApiUnavailableError):
        _HttpTransport("http://server").json("GET", "/status")
    with pytest.raises(ApiUnavailableError):
        _HttpTransport("http://server").download("/archive", destination)

    def rejected(*_args, **_kwargs):
        raise _http_error(403)

    monkeypatch.setattr("urllib.request.urlopen", rejected)
    with pytest.raises(ApiRejectedError) as caught:
        _HttpTransport("http://server").download("/archive", destination)
    assert caught.value.status == 403


@pytest.mark.parametrize("legacy", [False, True])
def test_generation_gateway_recognizes_pending_generation(legacy):
    gateway = HttpGenerationGateway("http://server")
    gateway._transport = Mock()
    pending = ApiRejectedError(404, "not ready", {"error": {"code": "not_found"}})
    gateway._transport.json.side_effect = (
        [ApiRejectedError(404, "route missing"), pending] if legacy else [pending]
    )
    with pytest.raises(GenerationNotReady):
        gateway.current_manifest()
    assert gateway._transport.json.call_count == (2 if legacy else 1)


def test_windows_tray_uses_windowed_python_and_no_console_flag(monkeypatch, tmp_path):
    import papagui_client.gui.launcher as launcher

    monkeypatch.setattr(launcher, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(launcher, "sys", SimpleNamespace(executable=str(tmp_path / "python.exe")))
    pythonw = tmp_path / "pythonw.exe"
    pythonw.touch()
    process = Mock()
    monkeypatch.setattr(launcher.subprocess, "Popen", process)
    monkeypatch.setattr(launcher.subprocess, "CREATE_NEW_PROCESS_GROUP", 1, raising=False)
    monkeypatch.setattr(launcher.subprocess, "CREATE_NO_WINDOW", 2, raising=False)
    launcher.TrayProcessLauncher().show()
    assert process.call_args.args[0] == [str(pythonw), "-m", "papagui_client.entrypoints.tray"]
    assert process.call_args.kwargs["creationflags"] == 3
    assert process.call_args.kwargs["stdout"] == launcher.subprocess.DEVNULL


def test_generation_gateway_unknown_routes_are_errors():
    gateway = HttpGenerationGateway("http://server")
    gateway._transport = Mock()
    gateway._transport.json.side_effect = ApiRejectedError(404, "route missing")
    with pytest.raises(ApiRejectedError):
        gateway.current_manifest()


def test_generation_gateway_v2_v1_fallback_and_download_routes(tmp_path):
    payload = {
        "schema_version": 2,
        "created_at": "2026-09-02T10:00:00+00:00",
        "components": {
            "index": {
                "kind": "index",
                "generation": "i-1",
                "created_at": "2026-09-02T10:00:00+00:00",
                "archive": "index.zip",
                "size": 1,
                "sha256": "0" * 64,
            },
            "customers": None,
        },
    }
    gateway = HttpGenerationGateway("http://server")
    gateway._transport = Mock()
    gateway._transport.json.return_value = payload
    assert gateway.current_manifest().index.generation == "i-1"
    gateway._transport.json.assert_called_once_with("GET", "/v2/generations/current")

    legacy = {
        "schema_version": 1,
        "generation": "legacy-1",
        "created_at": "2026-09-02T10:00:00+00:00",
        "archive": "legacy.zip",
        "size": 1,
        "sha256": "1" * 64,
    }
    gateway._transport.json.reset_mock()
    gateway._transport.json.side_effect = [ApiRejectedError(404, "missing"), legacy]
    legacy_value = gateway.current_manifest()
    assert legacy_value.legacy_combined
    assert gateway._transport.json.call_count == 2
    gateway._transport.download.reset_mock()
    gateway.download_component(
        GenerationComponentKind.INDEX,
        legacy_value.index,
        tmp_path / "legacy.zip",
    )
    assert gateway._transport.download.call_args.args[0] == (
        "/v1/index/generations/legacy.zip"
    )

    gateway._transport.json.side_effect = ApiRejectedError(500, "broken")
    with pytest.raises(ApiRejectedError):
        gateway.current_manifest()

    component = GenerationComponentManifest(
        GenerationComponentKind.INDEX,
        "i-1",
        "2026-09-02T10:00:00+00:00",
        "archive.zip",
        1,
        "0" * 64,
    )
    gateway._transport.json.side_effect = None
    gateway._transport.json.return_value = payload
    gateway.current_manifest()
    gateway._transport.download.reset_mock()
    gateway.download_component(GenerationComponentKind.INDEX, component, tmp_path / "one.zip")
    gateway._transport.download.assert_called_once_with(
        "/v2/generations/index/i-1/archive", tmp_path / "one.zip"
    )
    object.__setattr__(component, "archive", "/custom/archive.zip")
    gateway.download_component(GenerationComponentKind.INDEX, component, tmp_path / "two.zip")
    assert gateway._transport.download.call_args.args[0] == "/custom/archive.zip"


def test_customer_gateway_reads_mutates_and_translates_conflicts():
    gateway = HttpCustomerGateway("http://server")
    gateway._transport = Mock()
    gateway._transport.json.return_value = {
        "customers": [_customer().to_dict(), "ignored"]
    }
    assert gateway.list_customers() == [_customer()]
    gateway._transport.json.return_value = {"items": [_customer().to_dict()]}
    assert gateway.list_customers() == [_customer()]

    gateway._transport.json.return_value = {"customer": _customer().to_dict()}
    assert gateway.get_customer(7) == _customer()
    gateway._transport.json.return_value = {"unexpected": "shape"}
    assert gateway.get_customer(7).display_name == ""
    gateway._transport.json.side_effect = ApiRejectedError(404, "missing")
    assert gateway.get_customer(99) is None
    gateway._transport.json.side_effect = ApiRejectedError(500, "broken")
    with pytest.raises(ApiRejectedError):
        gateway.get_customer(7)

    gateway._transport.json.side_effect = None
    for operation, customer, expected_method, expected_path in (
        (CustomerMutationKind.CREATE, _customer(None, 0), "POST", "/v2/customers"),
        (CustomerMutationKind.UPDATE, _customer(), "PUT", "/v2/customers/7"),
        (CustomerMutationKind.DELETE, _customer(), "DELETE", "/v2/customers/7"),
    ):
        gateway._transport.json.reset_mock()
        gateway._transport.json.return_value = (
            {} if operation is CustomerMutationKind.DELETE else {"customer": customer.to_dict()}
        )
        result = gateway.mutate(_mutation(operation, customer=customer))
        assert gateway._transport.json.call_args.args[:2] == (expected_method, expected_path)
        assert result.deleted is (operation is CustomerMutationKind.DELETE)
        assert result.customer == (None if result.deleted else customer)

    gateway._transport.json.side_effect = ApiRejectedError(
        409, "conflict", {"current": _customer(7, 3).to_dict()}
    )
    with pytest.raises(ApiConflictError) as conflict:
        gateway.mutate(_mutation(CustomerMutationKind.UPDATE))
    assert conflict.value.current.revision == 3
    gateway._transport.json.side_effect = ApiRejectedError(409, "conflict", {})
    with pytest.raises(ApiConflictError) as conflict:
        gateway.mutate(_mutation(CustomerMutationKind.DELETE))
    assert conflict.value.current is None
    gateway._transport.json.side_effect = ApiRejectedError(
        409,
        "idempotency conflict",
        {"error": {"code": "idempotency_conflict", "status": 409}},
    )
    with pytest.raises(ApiIdempotencyConflictError):
        gateway.mutate(_mutation(CustomerMutationKind.UPDATE))
    gateway._transport.json.side_effect = ApiRejectedError(401, "unauthorized")
    with pytest.raises(ApiRejectedError):
        gateway.mutate(_mutation(CustomerMutationKind.UPDATE))


def test_server_control_uses_client_token_and_validates_actions():
    gateway = HttpServerControlGateway("http://server")
    gateway._transport = Mock()
    gateway._transport.json.return_value = {"state": "online"}
    assert gateway.status() == {"state": "online"}
    gateway._transport.json.return_value = {"settings": {"interval_seconds": 900}}
    assert gateway.settings()["settings"]["interval_seconds"] == 900
    gateway.save_settings({"interval_seconds": 3_600})
    assert gateway._transport.json.call_args.args[2] == {
        "settings": {"interval_seconds": 3_600}
    }
    for action in ("start", "full_rebuild", "cancel", "delete", "restart_server"):
        gateway.index_action(action)
        assert len(gateway._transport.json.call_args.args) == 3
    with pytest.raises(ValueError):
        gateway.index_action("shell")


def test_client_settings_platform_defaults_environment_and_validation(monkeypatch, tmp_path):
    import papagui_client.config as config

    monkeypatch.setattr(config, "Path", type(tmp_path))
    monkeypatch.setattr(config, "os", SimpleNamespace(**vars(config.os)))
    monkeypatch.setattr(config, "sys", SimpleNamespace(**vars(config.sys)))
    monkeypatch.setattr(config.os, "name", "nt")
    assert default_data_root().name == "PapaGUI"
    monkeypatch.setattr(config.sys, "platform", "darwin")
    monkeypatch.setattr(config.os, "name", "posix")
    assert "Application Support" in str(default_data_root())
    monkeypatch.setattr(config.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert default_data_root() == tmp_path / "xdg" / "PapaGUI"

    settings = ClientSettings.from_environment(
        {
            "PAPAGUI_INDEX_SERVER_URL": "https://server.test/",
            "PAPAGUI_CLIENT_DATA_ROOT": str(tmp_path / "data"),
            "PAPAGUI_API_TOKEN": "token",
            "PAPAGUI_API_TIMEOUT_SECONDS": "4.5",
            "PAPAGUI_SYNC_INTERVAL_SECONDS": "3600",
            "PAPAGUI_SOURCE_MAPPINGS": json.dumps(
                {"primary": {"linux": str(tmp_path), "windows": "Z:/"}, "bad": "skip"}
            ),
        }
    )
    assert settings.cache_root == tmp_path / "data"
    assert settings.outbox_path == tmp_path / "data" / "customer-outbox.db"
    assert settings.timeout_seconds == 4.5
    assert len(settings.source_mappings) == 1

    for kwargs in (
        {"server_url": "ftp://server"},
        {"sync_interval_seconds": 899},
        {"sync_interval_seconds": 172_801},
        {"timeout_seconds": 0},
    ):
        with pytest.raises(ValueError):
            ClientSettings(**kwargs)
    for raw in ("broken", "[]"):
        with pytest.raises(ValueError):
            ClientSettings.from_environment({"PAPAGUI_SOURCE_MAPPINGS": raw})


def test_client_composition_finds_components_and_exposes_services(tmp_path):
    settings = ClientSettings(data_root=tmp_path)
    container = ClientContainer(settings)
    assert container.settings is settings
    assert container.catalog_search is container.data_session.current.catalog_search
    assert container.customer_store is container.data_session.current.customers
    assert container._generation_ids() == {}
    assert container.catalog_database() == Path("__papagui_no_active_generation__")
    assert container.customer_database() == Path("__papagui_no_active_generation__")

    root = tmp_path / "component"
    (root / "catalog").mkdir(parents=True)
    database = root / "catalog" / "active.db"
    database.touch()
    assert container._first_file(root, "missing.db", "catalog/active.db") == database
    assert container._first_file(root, "first.db") == root / "first.db"
    assert isinstance(build_client(settings), ClientContainer)
    assert build_client(settings).server_control is None
    tray_container = build_tray(settings)
    assert tray_container.server_control is not None
    assert not hasattr(tray_container.review_gateway, "login_admin")


def test_client_composition_migrates_nested_generation_layout_atomically(tmp_path):
    data = tmp_path / "data"
    legacy_root = data / "generations"
    payload = legacy_root / "generations" / "index" / "i-1"
    payload.mkdir(parents=True)
    (payload / "catalog.db").write_text("immutable", encoding="utf-8")
    (legacy_root / "active-generation.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "components": {
                    "index": {"generation": "i-1", "path": "generations/index/i-1"}
                },
                "history": {"index": ["i-1"]},
            }
        ),
        encoding="utf-8",
    )

    container = ClientContainer(ClientSettings(data_root=data))

    assert (data / "active-generation.json").is_file()
    assert (data / "generations" / "index" / "i-1" / "catalog.db").read_text() == "immutable"
    assert not (data / "generations" / "generations").exists()
    assert container.generation_store.current_generation(GenerationComponentKind.INDEX) == "i-1"


def test_client_entrypoint_sync_and_gui_paths(monkeypatch, capsys):
    class Sync:
        def __init__(self, result=SyncResult(), error=None, local=True):
            self.result = result
            self.error = error
            self.local = local

        def sync(self):
            if self.error:
                raise self.error
            return self.result

        def has_local_data(self):
            return self.local

    holder = SimpleNamespace(sync=Sync(SyncResult(("index",), {"index": "i-1"})))
    monkeypatch.setattr(client_entrypoint, "build_client", lambda: holder)
    assert client_entrypoint.main(["--sync-only"]) == 0
    assert "Neue Datengeneration" in capsys.readouterr().out
    holder.sync = Sync()
    assert client_entrypoint.main(["--sync-only"]) == 0
    assert "aktuell" in capsys.readouterr().out
    holder.sync = Sync(error=SyncError("offline"), local=True)
    assert client_entrypoint.main(["--sync-only"]) == 0
    assert "lokale Generation bleibt aktiv" in capsys.readouterr().err
    holder.sync = Sync(error=SyncError("offline"), local=False)
    assert client_entrypoint.main(["--sync-only"]) == 1
    assert "Keine verwendbare" in capsys.readouterr().err

    called = []
    monkeypatch.setattr(
        "papagui_client.gui.main.run_main_gui",
        lambda value, automatic_sync: called.append((value, automatic_sync)) or 17,
    )
    assert client_entrypoint.main(["--offline"]) == 17
    assert called == [(holder, False)]


def test_entrypoint_import_errors_tray_forwarding_and_launch_commands(monkeypatch, capsys):
    import builtins
    import papagui_client.gui.main as gui_main
    import papagui_client.gui.tray as gui_tray

    original_import = builtins.__import__

    def missing_gui(name, *args, **kwargs):
        if name in {"papagui_client.gui.main", "papagui_client.gui.tray"}:
            raise ImportError("Qt absent")
        return original_import(name, *args, **kwargs)

    holder = object()
    monkeypatch.setattr(client_entrypoint, "build_client", lambda: holder)
    monkeypatch.setattr(tray_entrypoint, "build_tray", lambda: holder)
    with patch.object(builtins, "__import__", side_effect=missing_gui):
        assert client_entrypoint.main([]) == 2
        assert tray_entrypoint.main([]) == 2
    assert "nicht installiert" in capsys.readouterr().err

    calls = []
    monkeypatch.setattr(gui_tray, "run_tray_gui", lambda value, argv: calls.append((value, argv)) or 4)
    assert tray_entrypoint.main(["--background"]) == 4
    assert calls == [(holder, ["--background"])]
    monkeypatch.setattr(gui_main, "run_main_gui", lambda *_a, **_k: 0)

    launcher = TrayProcessLauncher()
    import papagui_client.gui.launcher as launcher_module
    monkeypatch.setattr(launcher_module, "os", SimpleNamespace(**vars(launcher_module.os)))
    monkeypatch.setattr(launcher_module, "sys", SimpleNamespace(**vars(launcher_module.sys)))
    monkeypatch.setattr("papagui_client.gui.launcher.sys.frozen", False, raising=False)
    assert launcher.command()[1:] == ["-m", "papagui_client.entrypoints.tray", "--background"]
    monkeypatch.setattr(launcher_module, "Path", PurePosixPath)
    monkeypatch.setattr("papagui_client.gui.launcher.sys.frozen", True, raising=False)
    monkeypatch.setattr("papagui_client.gui.launcher.sys.executable", "/opt/PapaGUI/papagui-client")
    monkeypatch.setattr("papagui_client.gui.launcher.os.name", "posix")
    assert launcher.command() == ["/opt/PapaGUI/papagui-tray", "--background"]
    monkeypatch.setattr("papagui_client.gui.launcher.sys.platform", "darwin")
    monkeypatch.setattr(
        "papagui_client.gui.launcher.sys.executable",
        "/Applications/PapaGUI Client.app/Contents/MacOS/papagui-client",
    )
    assert launcher.command() == [
        "/Applications/PapaGUI Tray.app/Contents/MacOS/papagui-tray",
        "--background",
    ]
    monkeypatch.setattr("papagui_client.gui.launcher.sys.platform", "linux")
    monkeypatch.setattr(
        "papagui_client.gui.launcher.sys.executable", "/opt/PapaGUI/papagui-client"
    )
    monkeypatch.setattr("papagui_client.gui.launcher.os.name", "nt")
    assert launcher.command() == ["/opt/PapaGUI/papagui-tray.exe", "--background"]

    popen = Mock()
    monkeypatch.setattr("papagui_client.gui.launcher.subprocess.Popen", popen)
    monkeypatch.setattr(
        "papagui_client.gui.launcher.subprocess.CREATE_NEW_PROCESS_GROUP", 1, raising=False
    )
    monkeypatch.setattr(
        "papagui_client.gui.launcher.subprocess.CREATE_NO_WINDOW", 2, raising=False
    )
    launcher.start()
    assert popen.call_args.kwargs["creationflags"]
    monkeypatch.setattr("papagui_client.gui.launcher.os.name", "posix")
    launcher.start()
    assert popen.call_args.kwargs["start_new_session"] is True
