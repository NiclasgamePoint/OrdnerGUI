from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from papagui_contracts import Contact, Customer
from papagui_client.application.models import SyncResult
from papagui_client.gui.customer_editor import CustomerEditorDialog
from papagui_client.gui.main import ClientMainWindow
from papagui_client.gui.tray import ServerTrayWindow
from papagui_client.presentation.coordinators import NavigationCoordinator
from papagui_client.presentation.tray import TrayHealth, TrayPresenter


def application():
    return QApplication.instance() or QApplication([])


class FakeSync:
    def __init__(self):
        self.started = 0
        self.stopped = 0

    def start(self, timer, callback, immediate=True):
        self.started += 1
        self.timer = timer
        timer.start(900, callback)
        # Avoid starting a worker in this lifecycle test; coordinator scheduling
        # itself is covered separately.

    def sync(self):
        return SyncResult()

    def stop(self):
        self.stopped += 1
        if hasattr(self, "timer"):
            self.timer.stop()


class FakeCustomers:
    def __init__(self):
        self.resolutions = []

    def list(self):
        return ()

    def conflicts(self):
        return ()

    def reload_from_server(self, case):
        self.resolutions.append(("reload", case))


class FakeSearch:
    def search(self, _text, **_filters):
        return ()


class FakeServerControl:
    def status(self):
        return {"state": "online", "index": {"state": "idle"}}


def container():
    return SimpleNamespace(
        settings=SimpleNamespace(server_url="http://server", data_root="/tmp/client"),
        sync=FakeSync(),
        search=FakeSearch(),
        customers=FakeCustomers(),
        navigation=NavigationCoordinator(),
        server_control=FakeServerControl(),
    )


def test_main_window_owns_interval_timer_and_stops_on_close():
    app = application()
    value = container()
    window = ClientMainWindow(value, automatic_sync=True)
    assert value.sync.started == 1
    assert window._sync_timer.active
    assert window._sync_timer.interval_seconds == 900

    window.show()
    window.close()
    app.processEvents()

    assert value.sync.stopped == 1
    assert not window._sync_timer.active


def test_tray_presenter_status_colors_animation_and_interval_bounds():
    presenter = TrayPresenter()
    assert presenter.offline("down").health is TrayHealth.OFFLINE
    assert presenter.status({"state": "degraded", "index": {"state": "idle"}}).health is TrayHealth.PROBLEM
    running = presenter.status({"state": "online", "index": {"state": "running"}})
    assert running.health is TrayHealth.RUNNING
    assert running.color == "#269d69"
    assert running.badge.startswith(("◔", "◑", "◕", "◐"))
    assert presenter.interval_fields(900) == (15, "Minuten")
    assert presenter.interval_fields(172_800) == (48, "Stunden")
    assert presenter.interval_seconds(15, "Minuten") == 900
    assert presenter.interval_seconds(48, "Stunden") == 172_800


def test_tray_window_applies_view_model_and_shuts_down_cleanly():
    app = application()
    value = container()
    window = ServerTrayWindow(value)
    window._show_status(
        {
            "state": "online",
            "server_version": "0.4.2",
            "index": {"state": "running", "progress": {"processed_items": 2, "total_items": 3}},
        }
    )
    assert "INDEXLAUF" in window.state.text()
    assert window._animation.isActive()
    window._apply_settings({"settings": {"interval_seconds": 7_200}})
    assert window.interval_value.value() == 2
    assert window.interval_unit.currentText() == "Stunden"

    window.shutdown()
    window.close()
    app.processEvents()
    assert not window._timer.isActive()
    assert not window._animation.isActive()


def test_conflict_dialog_exposes_all_explicit_resolution_paths():
    app = application()
    value = container()
    window = ClientMainWindow(value, automatic_sync=False)
    labels = []

    class Dialog:
        Icon = QMessageBox.Icon
        ButtonRole = QMessageBox.ButtonRole
        StandardButton = QMessageBox.StandardButton

        def __init__(self, _parent):
            self.buttons = []

        def setIcon(self, _value): pass
        def setWindowTitle(self, _value): pass
        def setText(self, _value): pass
        def setInformativeText(self, _value): pass

        def addButton(self, label, _role=None):
            if isinstance(label, str):
                button = object()
                labels.append(label)
                self.buttons.append(button)
                return button
            return object()

        def exec(self): return 0
        def clickedButton(self): return self.buttons[0]

        @staticmethod
        def warning(*_args): return None

    case = SimpleNamespace(
        local=None,
        current=None,
        mutation=SimpleNamespace(idempotency_key="key"),
    )
    with patch("papagui_client.gui.main.QMessageBox", Dialog):
        window._resolve_conflict(case)

    assert labels == [
        "Neu laden",
        "Manuell zusammenführen",
        "Erneut speichern",
        "Lokale Änderung verwerfen",
    ]
    assert value.customers.resolutions == [("reload", case)]
    window.close()
    app.processEvents()


def test_customer_editor_round_trips_all_core_customer_collections():
    application()
    original = Customer(
        id=7,
        revision=3,
        display_name="Muster",
        entity_type="Unternehmen",
        folder_path="source://primary/muster",
        folder_paths=("source://primary/muster",),
        service_types=("Beratung",),
        contacts=(Contact("Ada", "Einkauf", "ada@example.test", "123"),),
        notes=("Notiz",),
        tags=("A", "B"),
    )
    dialog = CustomerEditorDialog(original)

    result = dialog.customer()

    assert result == original
    dialog.close()
