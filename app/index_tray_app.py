"""Standalone system-tray application for the PapaGUI index server."""

from __future__ import annotations

import argparse
import os
import sys

from PySide6.QtCore import QIODevice
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon

from app.core.config import INDEX_LAYOUT
from app.core.logging_config import LOG_FILE, configure_logging
from app.gui.index_tray_window import IndexTrayWindow
from app.gui.theme import ThemeManager


INSTANCE_NAME = "papagui-index-server-tray"


class StandaloneIndexTray:
    """Own the tray icon and server window without creating a MainWindow."""

    def __init__(self, app: QApplication, show_window: bool = True):
        self.app = app
        self.window = IndexTrayWindow(
            INDEX_LAYOUT.jobs_dir / "catalog",
            LOG_FILE,
            content_state_dir=INDEX_LAYOUT.jobs_dir / "content",
            server_url=os.getenv("PAPAGUI_INDEX_SERVER_URL", "http://127.0.0.1:8765"),
            api_token=os.getenv("PAPAGUI_API_TOKEN", ""),
        )
        self.tray_icon = QSystemTrayIcon(app)
        self.tray_icon.setIcon(
            app.style().standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon)
        )
        self.tray_icon.setToolTip("PapaGUI Indexserver")
        menu = QMenu()
        open_action = menu.addAction("Indexserver öffnen")
        open_action.triggered.connect(self.show)
        menu.addSeparator()
        quit_action = menu.addAction("Indextray beenden")
        quit_action.triggered.connect(app.quit)
        self.tray_icon.setContextMenu(menu)
        self.tray_icon.activated.connect(self._activated)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray_icon.show()
        if show_window:
            self.show()
        app.aboutToQuit.connect(self.shutdown)

    def show(self):
        self.window.show_status()

    def _activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show()

    def shutdown(self):
        self.tray_icon.hide()
        self.window.shutdown()


class SingleInstanceServer:
    """Forward later launches to the already running tray process."""

    def __init__(self, name: str = INSTANCE_NAME):
        self.name = name
        self.server = QLocalServer()

    def claim(self, on_show) -> bool:
        if self.server.listen(self.name):
            self.server.newConnection.connect(lambda: self._receive(on_show))
            return True
        socket = QLocalSocket()
        socket.connectToServer(self.name, QIODevice.OpenModeFlag.WriteOnly)
        if socket.waitForConnected(300):
            socket.write(b"show")
            socket.waitForBytesWritten(300)
            socket.disconnectFromServer()
            return False
        QLocalServer.removeServer(self.name)
        if not self.server.listen(self.name):
            # Some sandboxed or hardened desktops forbid local Unix sockets.
            # The tray remains usable there, only second-launch activation is absent.
            return True
        self.server.newConnection.connect(lambda: self._receive(on_show))
        return True

    def _receive(self, on_show):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket is not None:
                socket.waitForReadyRead(100)
                if bytes(socket.readAll()).strip() == b"show":
                    on_show()
                socket.deleteLater()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PapaGUI Indexserver-Systemtray")
    parser.add_argument(
        "--background",
        action="store_true",
        help="nur das Tray-Icon starten und das Fenster zunächst geschlossen lassen",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    configure_logging()
    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("PapaGUI Indexserver")
    app.setQuitOnLastWindowClosed(False)
    ThemeManager().apply(app)
    controller = StandaloneIndexTray(app, show_window=not arguments.background)
    instance = SingleInstanceServer()
    if not instance.claim(controller.show):
        return 0
    # Keep Python ownership explicit for the complete Qt event-loop lifetime.
    app._papagui_index_tray = (controller, instance)  # type: ignore[attr-defined]
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
