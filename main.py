import sys
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from app.core.config import forced_fullscreen
from app.core.logging_config import configure_logging
from app.gui.main_window import MainWindow


def show_main_window(app: QApplication, window: MainWindow):
    if not forced_fullscreen():
        window.show()
        return

    screen = window.screen() or app.primaryScreen()
    if screen is not None:
        geometry = screen.availableGeometry()
        window.setGeometry(geometry)
    window.show()
    if screen is not None:
        geometry = screen.availableGeometry()
        window.move(geometry.topLeft())
        window.resize(geometry.size())
    window.showFullScreen()


def main():
    configure_logging()
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    window = MainWindow()
    show_main_window(app, window)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
