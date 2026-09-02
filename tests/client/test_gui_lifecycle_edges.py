from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon
import pytest

from papagui_contracts import Customer
from papagui_client.application.models import (
    CustomerConflict,
    CustomerSyncState,
    CustomerView,
    CustomerWriteResult,
    ReplayResult,
    SyncResult,
)
from papagui_client.gui.main import ClientMainWindow
from papagui_client.gui.tasks import BackgroundTask
from papagui_client.gui.tray import ServerTrayWindow, SingleInstanceServer, TrayController
from papagui_client.presentation.coordinators import ClientPage, ConflictCase, NavigationCoordinator


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


class Sync:
    def __init__(self):
        self.stopped = 0

    def start(self, *_args, **_kwargs):
        return None

    def stop(self):
        self.stopped += 1

    def sync(self):
        return SyncResult()


class Customers:
    def __init__(self):
        self.views = ()
        self.cases = ()
        self.saved = []
        self.deleted = []
        self.replayed = ReplayResult()
        self.raise_on = ""
        self.resolutions = []

    def list(self):
        if self.raise_on == "list":
            raise OSError("snapshot missing")
        return self.views

    def save(self, customer, **kwargs):
        if self.raise_on == "save":
            raise OSError("save failed")
        self.saved.append((customer, kwargs))
        return CustomerWriteResult(customer, False, ReplayResult())

    def delete(self, customer, **kwargs):
        if self.raise_on == "delete":
            raise OSError("delete failed")
        self.deleted.append((customer, kwargs))
        return CustomerWriteResult(None, False, ReplayResult())

    def replay(self):
        if self.raise_on == "replay":
            raise OSError("replay failed")
        return self.replayed

    def conflicts(self):
        return self.cases

    def reload_from_server(self, case):
        self.resolutions.append(("reload", case))

    def discard_local(self, case):
        self.resolutions.append(("discard", case))

    def retry_against_current(self, case):
        self.resolutions.append(("retry", case))

    def save_merge(self, case, customer):
        self.resolutions.append(("merge", case, customer))


class Search:
    def search(self, *_args, **_kwargs):
        return ()


class ServerControl:
    def __init__(self):
        self.admin_unlocked = False
        self.login_error = None
        self.actions = []
        self.logged_out = 0

    def status(self):
        return {"state": "online", "index": {"state": "idle"}}

    def login_admin(self, _password):
        if self.login_error:
            raise self.login_error
        self.admin_unlocked = True

    def index_action(self, action):
        self.actions.append(action)
        return {"ok": True}

    def settings(self):
        return {"settings": {"interval_seconds": 900}}

    def save_settings(self, settings):
        self.saved_settings = settings
        return {"settings": settings}

    def logout_admin(self):
        self.logged_out += 1
        self.admin_unlocked = False


def make_container(tmp_path):
    return SimpleNamespace(
        settings=SimpleNamespace(server_url="http://server", data_root=tmp_path),
        sync=Sync(),
        search=Search(),
        customers=Customers(),
        navigation=NavigationCoordinator(),
        server_control=ServerControl(),
    )


def test_background_task_emits_success_failure_and_finished(application):
    events = []
    task = BackgroundTask(lambda: 42)
    task.signals.succeeded.connect(lambda value: events.append(("ok", value)))
    task.signals.finished.connect(lambda: events.append(("done", None)))
    task.run()
    assert events == [("ok", 42), ("done", None)]

    events.clear()

    def fail():
        raise ValueError("boom")

    task = BackgroundTask(fail)
    task.signals.failed.connect(lambda value: events.append(("error", value)))
    task.signals.finished.connect(lambda: events.append(("done", None)))
    task.run()
    assert events == [("error", "boom"), ("done", None)]


def test_main_window_sync_status_and_customer_rendering(application, tmp_path):
    container = make_container(tmp_path)
    container.customers.views = (
        CustomerView("1", Customer(id=1, display_name="A", city="Berlin")),
        CustomerView(
            "local:x",
            Customer(display_name="B"),
            CustomerSyncState.PENDING,
        ),
        CustomerView(
            "2", Customer(id=2, display_name="C"), CustomerSyncState.CONFLICT
        ),
        CustomerView(
            "3",
            Customer(id=3, display_name="D"),
            CustomerSyncState.AWAITING_SNAPSHOT,
        ),
    )
    window = ClientMainWindow(container, automatic_sync=False)
    assert window.customer_list.count() == 4
    assert "2" in window.queue_status.text()

    tasks = []
    window._start_task = tasks.append
    window.synchronize()
    assert window._syncing
    assert len(tasks) == 1
    window.synchronize()  # in-flight guard
    window._sync_failed("offline")
    assert "Offline" in window.status.text()
    window._sync_finished()
    assert not window._syncing

    refreshed = []
    conflicts = []
    searches = []
    window.refresh_customers = lambda: refreshed.append(True)
    window._handle_conflicts = lambda values: conflicts.append(values)
    window.catalog_browser.run_search = lambda: searches.append(True)
    replay = ReplayResult(
        conflicts=(CustomerConflict("key", None, None),)
    )
    window.catalog_browser.query_input.setText("query")
    window._sync_complete(SyncResult(("index",), {}, replay))
    assert "index" in window.status.text()
    assert refreshed and conflicts and searches
    window.catalog_browser.query_input.clear()
    window._sync_complete(SyncResult())
    assert "aktuell" in window.status.text()

    container.customers.raise_on = "list"
    ClientMainWindow.refresh_customers(window)
    assert "nicht verfügbar" in window.customer_list.item(0).text()
    window._closing = True
    window.synchronize()
    window.close()


class Editor:
    class DialogCode:
        Accepted = 1

    accepted = True
    value = Customer(display_name="Edited")

    def __init__(self, *_args, **_kwargs):
        pass

    def exec(self):
        return self.DialogCode.Accepted if self.accepted else 0

    def customer(self):
        return self.value


def test_main_window_customer_actions_and_errors(application, tmp_path, monkeypatch):
    container = make_container(tmp_path)
    local = CustomerView("local:a", Customer(display_name="Local"))
    remote = CustomerView("7", Customer(id=7, revision=2, display_name="Remote"))
    container.customers.views = (local, remote)
    window = ClientMainWindow(container, automatic_sync=False)
    monkeypatch.setattr("papagui_client.gui.main.CustomerEditorDialog", Editor)

    window.new_customer()
    assert container.customers.saved[-1][0].display_name == "Edited"
    window.customer_list.setCurrentRow(0)
    window.edit_customer()
    assert container.customers.saved[-1][1]["local_key"] == "local:a"
    Editor.accepted = False
    window.new_customer()
    window.edit_customer()
    Editor.accepted = True
    window.customer_list.setCurrentRow(-1)
    window.edit_customer()
    window.delete_customer()

    warnings = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        lambda *_args: warnings.append(_args),
    )
    window.customer_list.setCurrentRow(1)
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args: QMessageBox.StandardButton.No,
    )
    window.delete_customer()
    assert not container.customers.deleted
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args: QMessageBox.StandardButton.Yes,
    )
    window.delete_customer()
    assert container.customers.deleted[-1][0].id == 7
    container.customers.raise_on = "delete"
    window.customer_list.setCurrentRow(1)
    window.delete_customer()
    container.customers.raise_on = "save"
    window._save_customer(Customer(display_name="X"))
    container.customers.raise_on = "replay"
    window.replay_customers()
    assert len(warnings) == 3

    container.customers.raise_on = ""
    handled = []
    window._handle_conflicts = lambda values: handled.append(values)
    window.refresh_customers = lambda: handled.append("refresh")
    window.replay_customers()
    window._handle_write_result(CustomerWriteResult(None, False, ReplayResult()))
    assert "refresh" in handled
    window.close()


class ConflictDialog:
    Icon = QMessageBox.Icon
    ButtonRole = QMessageBox.ButtonRole
    StandardButton = QMessageBox.StandardButton
    choice = "reload"

    def __init__(self, _parent):
        self.buttons = {}

    def setIcon(self, _value):
        pass

    def setWindowTitle(self, _value):
        pass

    def setText(self, _value):
        pass

    def setInformativeText(self, _value):
        pass

    def addButton(self, label, _role=None):
        value = object()
        if isinstance(label, str):
            names = {
                "Neu laden": "reload",
                "Manuell zusammenführen": "merge",
                "Erneut speichern": "retry",
                "Lokale Änderung verwerfen": "discard",
            }
            self.buttons[names[label]] = value
        return value

    def exec(self):
        return 0

    def clickedButton(self):
        return self.buttons[self.choice]

    @staticmethod
    def warning(*_args):
        pass


def test_main_window_conflict_navigation_and_task_lifecycle(application, tmp_path, monkeypatch):
    container = make_container(tmp_path)
    mutation = SimpleNamespace(idempotency_key="key")
    case = ConflictCase(
        "7",
        mutation,
        Customer(id=7, revision=1, display_name="Local"),
        Customer(id=7, revision=2, display_name="Server"),
    )
    container.customers.cases = (case,)
    window = ClientMainWindow(container, automatic_sync=False)
    monkeypatch.setattr("papagui_client.gui.main.QMessageBox", ConflictDialog)
    monkeypatch.setattr("papagui_client.gui.main.CustomerEditorDialog", Editor)

    for choice in ("reload", "discard", "retry", "merge"):
        ConflictDialog.choice = choice
        window._resolve_conflict(case)
    assert [entry[0] for entry in container.customers.resolutions] == [
        "reload",
        "discard",
        "retry",
        "merge",
    ]
    window._handle_conflicts(())

    window._tab_changed(1)
    assert container.navigation.current is ClientPage.CUSTOMERS
    window._tab_changed(99)
    window._navigate(ClientPage.CONNECTION)
    assert window.tabs.currentIndex() == 2

    task = BackgroundTask(lambda: None)
    pool = Mock()
    window._pool = pool
    window._start_task(task)
    assert task in window._tasks
    pool.start.assert_called_once_with(task)
    with patch.object(window, "sender", return_value=task.signals):
        window._task_finished()
    assert task not in window._tasks

    class BrokenSignals:
        def disconnect(self):
            raise RuntimeError("deleted")

    class BrokenTask:
        signals = BrokenSignals()

    window._tasks = {BrokenTask()}
    pool.waitForDone = Mock()
    window.close()
    pool.clear.assert_called()


def test_run_main_gui_success_and_detached_tray_failure(monkeypatch, tmp_path):
    import papagui_client.gui.main as main

    class App:
        def __init__(self):
            self.names = []

        def setApplicationName(self, value):
            self.names.append(value)

        def setStyleSheet(self, value):
            self.style = value

        def exec(self):
            return 23

    app = App()

    class Application:
        @staticmethod
        def instance():
            return app

    class Window:
        def __init__(self, container, automatic_sync):
            self.values = (container, automatic_sync)

        def show(self):
            self.shown = True

    monkeypatch.setattr(main, "QApplication", Application)
    monkeypatch.setattr(main, "ClientMainWindow", Window)
    launcher = Mock()
    monkeypatch.setattr(main, "TrayProcessLauncher", lambda: launcher)
    container = SimpleNamespace(settings=SimpleNamespace(theme="system"))
    assert main.run_main_gui(container, automatic_sync=False) == 23
    assert app._papagui_window.shown
    launcher.start.side_effect = OSError("blocked")
    assert main.run_main_gui(container) == 23


def test_tray_window_actions_admin_and_shutdown(application, tmp_path, monkeypatch):
    container = make_container(tmp_path)
    window = ServerTrayWindow(container)
    tasks = []
    window._start_task = tasks.append
    window.refresh_status()
    assert window._refreshing and len(tasks) == 1
    window.refresh_status()
    window._refresh_finished()
    window._show_offline("down")
    assert "OFFLINE" in window.state.text()
    window._animate_running_badge()
    window._show_status({"state": "online", "index": {"state": "running"}})
    window._show_status({"state": "online", "index": {"state": "idle"}})

    window._ensure_admin = lambda: False
    window.run_action("start")
    window.load_settings()
    window.save_settings()
    assert len(tasks) == 1
    window._ensure_admin = lambda: True
    window.run_action("start")
    window.load_settings()
    window._apply_settings({"interval_seconds": 3600})
    window._settings_finished()
    assert window.interval_unit.currentText() == "Stunden"
    window.save_settings()
    assert len(tasks) == 4
    window._update_interval_range("Minuten")
    assert window.interval_value.minimum() == 15
    window._update_interval_range("Stunden")
    assert window.interval_value.maximum() == 48

    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: errors.append(_args))
    window._show_action_error("bad")
    window.refresh_status = lambda: errors.append("refresh")
    window._action_complete(None)
    assert errors

    pool = Mock()
    window._pool = pool
    task = BackgroundTask(lambda: None)
    ServerTrayWindow._start_task(window, task)
    with patch.object(window, "sender", return_value=task.signals):
        window._task_finished()
    assert task not in window._tasks
    window.shutdown()
    assert window._shutting_down
    window.close()


def test_tray_owns_recognition_admin_review(application, tmp_path, monkeypatch):
    container = make_container(tmp_path)
    opened = []

    class ReviewDialog:
        def __init__(self, control, customers, parent):
            opened.append((control, customers, parent))

        def exec(self):
            opened.append("exec")

    monkeypatch.setattr(
        "papagui_client.gui.tray.RecognitionReviewDialog", ReviewDialog
    )
    window = ServerTrayWindow(container)
    window.open_recognition_review()
    assert opened[0] == (container.server_control, (), window)
    assert opened[1] == "exec"

    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: warnings.append(_args))
    control = container.server_control
    container.server_control = None
    window.open_recognition_review()
    assert "keine Admin-Steuerung" in warnings[-1][2]
    container.server_control = control
    window.shutdown()
    window.close()


def test_tray_admin_prompt_controller_and_logout(application, tmp_path, monkeypatch):
    container = make_container(tmp_path)
    window = ServerTrayWindow(container)
    monkeypatch.setattr(
        "papagui_client.gui.tray.QInputDialog.getText", lambda *_args: ("", False)
    )
    assert not window._ensure_admin()
    monkeypatch.setattr(
        "papagui_client.gui.tray.QInputDialog.getText", lambda *_args: ("secret", True)
    )
    errors = []
    window._show_action_error = errors.append
    container.server_control.login_error = ValueError("wrong")
    assert not window._ensure_admin()
    assert errors == ["wrong"]
    container.server_control.login_error = None
    assert window._ensure_admin()
    assert window._ensure_admin()  # already unlocked
    window.close()

    with patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False):
        controller = TrayController(application, container, show=False)
    controller.show()
    controller._activated(QSystemTrayIcon.ActivationReason.Context)
    controller._activated(QSystemTrayIcon.ActivationReason.Trigger)
    controller.shutdown()
    assert container.server_control.logged_out == 1

    container.server_control.admin_unlocked = True
    container.server_control.logout_admin = Mock(side_effect=OSError("offline"))
    with patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=False):
        controller = TrayController(application, container, show=True)
    controller.shutdown()
    container.server_control.logout_admin.assert_called_once()


class Signal:
    def __init__(self):
        self.callback = None

    def connect(self, callback):
        self.callback = callback


class LocalServer:
    listen_results = [True]
    pending = []

    def __init__(self):
        self.newConnection = Signal()

    def listen(self, _name):
        return self.listen_results.pop(0)

    def hasPendingConnections(self):
        return bool(self.pending)

    def nextPendingConnection(self):
        return self.pending.pop(0)

    @staticmethod
    def removeServer(_name):
        return True


class LocalSocket:
    connects = True
    instances = []

    def __init__(self, payload=b"show"):
        self.payload = payload
        self.writes = []
        self.instances.append(self)

    def connectToServer(self, *_args):
        pass

    def waitForConnected(self, _timeout):
        return self.connects

    def write(self, value):
        self.writes.append(value)

    def waitForBytesWritten(self, _timeout):
        return True

    def disconnectFromServer(self):
        pass

    def waitForReadyRead(self, _timeout):
        return True

    def readAll(self):
        return self.payload

    def deleteLater(self):
        self.deleted = True


def test_single_instance_all_claim_and_receive_paths(monkeypatch):
    import papagui_client.gui.tray as tray

    monkeypatch.setattr(tray, "QLocalServer", LocalServer)
    monkeypatch.setattr(tray, "QLocalSocket", LocalSocket)
    LocalServer.listen_results = [True]
    assert SingleInstanceServer().claim(lambda: None)

    LocalServer.listen_results = [False]
    LocalSocket.connects = True
    foreground = SingleInstanceServer()
    assert not foreground.claim(lambda: None)
    assert foreground.server is not None
    assert LocalSocket.instances[-1].writes == [b"show"]

    LocalServer.listen_results = [False]
    background = SingleInstanceServer()
    assert not background.claim(lambda: None, request_show=False)
    assert LocalSocket.instances[-1].writes == []

    LocalServer.listen_results = [False, False]
    LocalSocket.connects = False
    assert SingleInstanceServer().claim(lambda: None)
    LocalServer.listen_results = [False, True]
    claimed = SingleInstanceServer()
    assert claimed.claim(lambda: None)

    shown = []
    first = LocalSocket(b"ignore")
    second = LocalSocket(b"show")
    claimed.server.pending = [first, None, second]
    claimed._receive(lambda: shown.append(True))
    assert shown == [True]
    assert first.deleted and second.deleted


def test_run_tray_gui_second_instance_and_primary(monkeypatch):
    import papagui_client.gui.tray as tray

    class App:
        def setApplicationName(self, _value):
            pass

        def setQuitOnLastWindowClosed(self, _value):
            pass

        def setStyleSheet(self, _value):
            pass

        def exec(self):
            return 31

    app = App()

    class Application:
        @staticmethod
        def instance():
            return app

    controller = SimpleNamespace(show=lambda: None)
    monkeypatch.setattr(tray, "QApplication", Application)
    monkeypatch.setattr(tray, "TrayController", lambda *_args, **_kwargs: controller)

    requests = []
    instance = SimpleNamespace(
        claim=lambda _show, *, request_show=True: requests.append(request_show) or False
    )
    monkeypatch.setattr(tray, "SingleInstanceServer", lambda: instance)
    assert tray.run_tray_gui(object(), ["--background"]) == 0
    assert requests == [False]
    instance.claim = (
        lambda _show, *, request_show=True: requests.append(request_show) or True
    )
    assert tray.run_tray_gui(object(), []) == 31
    assert requests[-1] is True
    assert app._papagui_tray[0] is controller
