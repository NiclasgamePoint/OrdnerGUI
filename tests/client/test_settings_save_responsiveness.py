from __future__ import annotations

from dataclasses import replace
from threading import Event

from PySide6.QtCore import QThread, QTimer
from PySide6.QtWidgets import QMessageBox, QSystemTrayIcon
import pytest

from papagui_client.application.paths import SourceMapping
from papagui_client.application.models import SyncResult
from papagui_client.composition import ClientContainer
from papagui_client.config import ClientSettings, ClientTheme
from papagui_client.gui import main as main_module
from papagui_client.gui.main import ClientMainWindow
from papagui_client.gui.settings import ClientSettingsDialog
from papagui_client.gui.theme import ThemeManager
from papagui_client.gui.timers import QtTimerAdapter


class SlowRepository:
    def __init__(self, settings):
        self.settings = settings
        self.entered = Event()
        self.release = Event()
        self.thread = None
        self.calls = 0
        self.error = None

    def save(self, settings):
        self.calls += 1
        self.thread = QThread.currentThread()
        self.entered.set()
        if not self.release.wait(5):
            raise TimeoutError("Test did not release the configuration store")
        if self.error:
            raise self.error
        self.settings = settings

    def resolve(self):
        return self.settings.with_environment({})


@pytest.fixture
def save_window(qtbot, tmp_path, monkeypatch):
    settings = ClientSettings(
        data_root=tmp_path,
        source_mappings=(SourceMapping("primary", windows="Z:/Archive", linux="/mnt/archive"),),
        theme=ClientTheme.LIGHT,
    )
    repository = SlowRepository(settings)
    container = ClientContainer(settings, config_repository=repository)
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", lambda: False)
    window = ClientMainWindow(container, automatic_sync=False)
    qtbot.addWidget(window)
    try:
        yield window, repository
    finally:
        repository.release.set()
        if window._syncing:
            window._sync_finished()
        qtbot.waitUntil(lambda: not window._settings_saving, timeout=8000)


def test_save_button_keeps_event_loop_live_and_persists_appearance_in_worker(
    save_window, qtbot, qapp, monkeypatch
):
    window, repository = save_window
    dialog = ClientSettingsDialog(window._container.settings, parent=window)
    dialog.connection_page.server_url.setText("https://updated.test")
    dialog.font_size_slider.setValue(17)
    monkeypatch.setattr(main_module, "ClientSettingsDialog", lambda *args, **kwargs: dialog)
    theme_threads = []
    original_save = ThemeManager.save

    def save_theme(manager):
        theme_threads.append(QThread.currentThread())
        original_save(manager)

    monkeypatch.setattr(ThemeManager, "save", save_theme)
    monkeypatch.setattr(
        window, "_refresh_search_metadata",
        lambda: pytest.fail("Saving preferences must not requery the entire catalog"),
    )
    ticks = []
    heartbeat = QTimer(window)
    heartbeat.setInterval(10)
    heartbeat.timeout.connect(lambda: ticks.append(True))
    heartbeat.start()
    QTimer.singleShot(0, dialog.save_button.click)
    window.open_settings()
    qtbot.waitUntil(repository.entered.is_set)
    qtbot.waitUntil(lambda: len(ticks) >= 3)
    assert window._settings_saving
    assert repository.thread != qapp.thread()
    assert not theme_threads  # Appearance is not written before configuration succeeds.
    assert window._container.settings.server_url != "https://updated.test"
    assert not window.header.settings_button.isEnabled()

    # A duplicate save is ignored; closing must not discard a pending write.
    window._save_client_settings(window._container.settings)
    assert not window.close()
    assert repository.calls == 1
    repository.release.set()
    qtbot.waitUntil(lambda: not window._settings_saving)
    assert window._container.settings.server_url == "https://updated.test"
    assert window.header.settings_button.isEnabled()
    assert theme_threads and all(thread != qapp.thread() for thread in theme_threads)
    assert ThemeManager().font_size == 17
    heartbeat.stop()


def test_saved_connection_waits_for_sync_and_reschedules_timer_on_gui_thread(
    save_window, qtbot, qapp
):
    window, repository = save_window
    container = window._container
    calls = []

    class CheckedTimer(QtTimerAdapter):
        def start(self, interval_seconds, callback):
            assert QThread.currentThread() == qapp.thread()
            calls.append(interval_seconds)
            super().start(interval_seconds, callback)

        def stop(self):
            assert QThread.currentThread() == qapp.thread()
            super().stop()

    timer = CheckedTimer(window)
    container.sync.start(timer, window.synchronize, immediate=False)
    old_gateway = container.generation_gateway
    window._syncing = True
    repository.release.set()
    updated = replace(container.settings, server_url="https://new.test", sync_interval_seconds=1800)
    window._save_client_settings(updated)
    qtbot.waitUntil(lambda: window._saved_configuration is not None)
    assert container.generation_gateway is old_gateway
    assert calls == [900]
    window._sync_finished()
    assert not window._settings_saving
    assert container.settings == updated
    assert calls == [900, 1800]
    assert timer.active
    assert timer.interval_seconds == 1800


def test_failed_write_keeps_live_settings_and_allows_retry(save_window, qtbot, monkeypatch):
    window, repository = save_window
    original = window._container.settings
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    repository.error = OSError("disk full")
    repository.release.set()
    updated = replace(original, server_url="https://new.test")
    window._save_client_settings(updated)
    qtbot.waitUntil(lambda: not window._settings_saving)
    assert window._container.settings == original
    assert window.header.settings_button.isEnabled()
    assert warnings and "disk full" in warnings[0][-1]
    repository.error = None
    window._save_client_settings(updated)
    qtbot.waitUntil(lambda: not window._settings_saving)
    assert window._container.settings == updated


def test_sync_requested_during_save_retries_with_the_saved_connection(save_window, qtbot):
    window, repository = save_window
    calls = []

    def sync():
        calls.append(window._container.settings.server_url)
        return SyncResult()

    window._container.sync.sync = sync
    updated = replace(window._container.settings, server_url="https://new.test")
    window._save_client_settings(updated)
    qtbot.waitUntil(repository.entered.is_set)
    window.synchronize()
    assert not calls
    assert window._initial_index_timer.isActive()
    repository.release.set()
    qtbot.waitUntil(lambda: bool(calls), timeout=3000)
    assert calls == ["https://new.test"]


def test_unchanged_theme_does_not_repolish_every_widget(qapp, monkeypatch):
    manager = ThemeManager()
    manager.apply(qapp)
    stylesheets = []
    original = qapp.setStyleSheet
    monkeypatch.setattr(qapp, "setStyleSheet", lambda value: (stylesheets.append(value), original(value)))
    manager.apply(qapp)
    assert stylesheets == []
    manager.set_accent("#3366cc")
    manager.apply(qapp)
    assert len(stylesheets) == 1
    assert stylesheets[0]  # No intermediate reset to the unstyled widget tree.
